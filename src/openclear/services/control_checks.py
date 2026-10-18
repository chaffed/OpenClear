# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""The checks CONTROLS_PLAN.md CT2 adds, from data OpenClear already keeps (compliance/checks.py
lists them all). State checks look at the bank as it is now; population checks look at every
item in a period, so a bank can test a quarter, including one from before it turned this on.

Each returns a controls_service.Control: PASS, WARN or FAIL, a sentence for people, and the
evidence (counts, and names where a person would act on them)."""

import uuid
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from openclear.config import get_settings
from openclear.services.controls_service import ADMIN_PERMISSIONS, FAIL, PASS, SECRET_MAX_AGE_DAYS, WARN, Control

DORMANT_DAYS = 90
LIST_LIMIT = 50  # names in evidence, at most
SCHEDULE_SLACK = 2  # a job is late after twice its interval (and a nightly one after 26 hours)
DECIDE_PERMISSIONS = {"exception:decide", "exception:recommend", "issued_item:write", "paid_item:write", "ach_transaction:write"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime | None) -> datetime | None:
    return value.replace(tzinfo=timezone.utc) if value is not None and value.tzinfo is None else value


def _plural(count: int, word: str) -> str:
    return f"{count} {word}{'' if count == 1 else 's'}"


def _people(session: Session, tenant_id: uuid.UUID, *, customers: bool | None):
    """Active people (not API clients) with their membership and group's permissions: bank
    staff (customers=False), customers' people (True) or both (None)."""
    from openclear.domain.security_group import SecurityGroup
    from openclear.domain.tenant_membership import TenantMembership
    from openclear.domain.user import User

    stmt = (select(User, TenantMembership, SecurityGroup.permissions)
            .join(TenantMembership, TenantMembership.user_id == User.id)
            .outerjoin(SecurityGroup, SecurityGroup.id == TenantMembership.security_group_id)
            .where(TenantMembership.tenant_id == tenant_id, TenantMembership.is_active.is_(True), User.is_active.is_(True),
                   User.is_service_account.is_(False)))
    if customers is True:
        stmt = stmt.where(TenantMembership.customer_id.is_not(None))
    elif customers is False:
        stmt = stmt.where(TenantMembership.customer_id.is_(None))
    return [(user, membership, set(perms or [])) for user, membership, perms in session.execute(stmt).all()]


def _dormant(user, membership, now: datetime) -> bool:
    """No sign-in to this bank for DORMANT_DAYS (membership.last_active_at; before it was kept,
    the person's last sign-in anywhere)."""
    last = _aware(membership.last_active_at) or _aware(user.last_login_at) or _aware(membership.created_at)
    return last is not None and last < now - timedelta(days=DORMANT_DAYS)


def _inactivity_rule(session: Session, tenant_id: uuid.UUID) -> str | None:
    """The bank's automatic rule for inactive access, in words, or None (services/inactivity_service.py)."""
    from openclear.domain.tenant import Tenant

    tenant = session.get(Tenant, tenant_id)
    if tenant is None:
        return None
    parts = [f"deactivates access after {tenant.deactivate_inactive_days} days" if tenant.deactivate_inactive else "",
             f"removes it after {tenant.remove_inactive_days}" if tenant.remove_inactive else ""]
    parts = [p for p in parts if p]
    return " and ".join(parts) if parts else None


def _with_second_factor(session: Session, tenant_id: uuid.UUID, user_ids) -> set:
    from openclear.domain.webauthn_credential import WebauthnCredential

    ids = list(user_ids)
    if not ids:
        return set()
    return set(session.execute(select(WebauthnCredential.user_id).where(WebauthnCredential.tenant_id == tenant_id,
                                                                        WebauthnCredential.user_id.in_(ids))).scalars())


# --- People ------------------------------------------------------------------------------------


def dormant_accounts(session: Session, tenant_id: uuid.UUID) -> Control:
    now = _now()
    staff = _people(session, tenant_id, customers=False)
    dormant = sorted(u.email for u, m, _p in staff if _dormant(u, m, now))
    rule = _inactivity_rule(session, tenant_id)
    evidence = {"active_staff": len(staff), "no_sign_in_for_90_days": dormant[:LIST_LIMIT], "dormant": len(dormant), "automatic": rule}
    title = "Dormant staff accounts"
    if dormant:
        return Control("dormant_accounts", title, WARN,
                       f"{_plural(len(dormant), 'staff member')} haven't signed in for {DORMANT_DAYS} days: {', '.join(dormant[:10])}. "
                       + (f"Your rule {rule}." if rule else "Deactivate anyone who's left, or have OpenClear do it "
                                                            "(Settings → Lockout and inactive accounts)."), evidence)
    return Control("dormant_accounts", title, PASS, f"All {len(staff)} active staff signed in within {DORMANT_DAYS} days.", evidence)


def customer_dormant_users(session: Session, tenant_id: uuid.UUID) -> Control:
    now = _now()
    people = _people(session, tenant_id, customers=True)
    dormant = sorted(u.email for u, m, _p in people if _dormant(u, m, now))
    evidence = {"active_customer_people": len(people), "no_sign_in_for_90_days": dormant[:LIST_LIMIT], "dormant": len(dormant),
                "automatic": _inactivity_rule(session, tenant_id)}
    title = "Dormant customer accounts"
    if dormant:
        return Control("customer_dormant_users", title, WARN,
                       f"{_plural(len(dormant), 'customer person')} haven't signed in for {DORMANT_DAYS} days.", evidence)
    return Control("customer_dormant_users", title, PASS,
                   f"All {len(people)} active customer people signed in within {DORMANT_DAYS} days.", evidence)


def privileged_accounts(session: Session, tenant_id: uuid.UUID) -> Control:
    now = _now()
    admins = [(u, m) for u, m, perms in _people(session, tenant_id, customers=False) if perms & ADMIN_PERMISSIONS]
    dormant = sorted(u.email for u, m in admins if _dormant(u, m, now))
    evidence = {"admins": len(admins), "names": sorted(u.email for u, _m in admins)[:LIST_LIMIT], "dormant_admins": dormant}
    title = "Privileged accounts"
    if dormant:
        return Control("privileged_accounts", title, WARN,
                       f"{_plural(len(admins), 'person')} hold admin permissions; {len(dormant)} haven't signed in for "
                       f"{DORMANT_DAYS} days: {', '.join(dormant[:10])}.", evidence)
    return Control("privileged_accounts", title, PASS, f"{_plural(len(admins), 'person')} hold admin permissions, all in use.", evidence)


def staff_mfa(session: Session, tenant_id: uuid.UUID) -> Control:
    from openclear.domain.tenant import Tenant

    title = "Staff use a second factor"
    deciders = {u.id: u.email for u, _m, perms in _people(session, tenant_id, customers=False) if perms & DECIDE_PERMISSIONS}
    if not session.get(Tenant, tenant_id).password_login_enabled:
        return Control("staff_mfa", title, PASS, f"Password sign-in is off: all {len(deciders)} sign in through your identity provider.",
                       {"deciders": len(deciders), "sso_only": True})
    keys = _with_second_factor(session, tenant_id, deciders)
    without = sorted(email for uid, email in deciders.items() if uid not in keys)
    evidence = {"deciders": len(deciders), "with_security_key": len(keys), "without": without[:LIST_LIMIT]}
    if without:
        return Control("staff_mfa", title, WARN,
                       f"{len(without)} of {len(deciders)} staff who decide or change data have no security key or passkey.", evidence)
    return Control("staff_mfa", title, PASS, f"All {len(deciders)} staff who decide or change data have a security key or passkey.", evidence)


def security_key_required(session: Session, tenant_id: uuid.UUID) -> Control:
    """OW-D18: the bank requires a security key for password sign-ins, and it's in force."""
    from openclear.auth.security_key_policy import requirement
    from openclear.domain.tenant import Tenant

    tenant = session.get(Tenant, tenant_id)
    title = "A security key is required"
    evidence = {"required": tenant.require_security_key, "grace_days": tenant.security_key_grace_days,
                "required_since": tenant.security_key_required_since.isoformat() if tenant.security_key_required_since else None}
    if not tenant.password_login_enabled:
        return Control("security_key_required", title, PASS, "Password sign-in is off: your staff sign in through your identity provider.",
                       evidence)
    if not tenant.require_security_key:
        return Control("security_key_required", title, WARN,
                       "Password sign-ins don't require a security key or passkey (Settings → Required security keys).", evidence)
    people = _people(session, tenant_id, customers=None)
    keys = _with_second_factor(session, tenant_id, {u.id for u, _m, _p in people})
    waiting = sorted(u.email for u, m, _p in people if u.id not in keys and requirement(session, tenant, m).required
                     and not requirement(session, tenant, m).enforced)
    evidence["in_grace_without_a_key"] = waiting[:LIST_LIMIT]
    if waiting:
        return Control("security_key_required", title, WARN,
                       f"Required; {len(waiting)} people are still in the grace period without a security key.", evidence)
    return Control("security_key_required", title, PASS, "Required for every password sign-in.", evidence)


def customer_mfa(session: Session, tenant_id: uuid.UUID) -> Control:
    from openclear.domain.customer import Customer

    title = "Customer people use a second factor"
    people = _people(session, tenant_id, customers=True)
    sso_only = set(session.execute(select(Customer.id).where(Customer.tenant_id == tenant_id,
                                                             Customer.password_login_enabled.is_(False))).scalars())
    keys = _with_second_factor(session, tenant_id, {u.id for u, _m, _p in people})
    covered = [u for u, m, _p in people if m.customer_id in sso_only or u.id in keys]
    share = round(100 * len(covered) / len(people)) if people else 100
    evidence = {"customer_people": len(people), "with_second_factor_or_sso_only": len(covered), "percent": share}
    if people and len(covered) < len(people):
        return Control("customer_mfa", title, WARN,
                       f"{share}% of customer people sign in with a security key or their organization's identity provider "
                       f"({len(covered)} of {len(people)}).", evidence)
    return Control("customer_mfa", title, PASS,
                   f"All {len(people)} customer people use a security key or their organization's identity provider.", evidence)


def customer_sod(session: Session, tenant_id: uuid.UUID) -> Control:
    from openclear.domain.customer import Customer

    title = "Customers separate issuing from deciding"
    both = [(u, m) for u, m, perms in _people(session, tenant_id, customers=True)
            if "issued_item:write" in perms and ("exception:decide" in perms or "exception:recommend" in perms)]
    names = {c.id: c.name for c in session.execute(select(Customer).where(Customer.tenant_id == tenant_id)).scalars()}
    listed = sorted(f"{u.email} ({names.get(m.customer_id, '?')})" for u, m in both)
    evidence = {"people_who_issue_and_decide": listed[:LIST_LIMIT], "count": len(listed)}
    if listed:
        return Control("customer_sod", title, WARN,
                       f"{_plural(len(listed), 'customer person')} can both submit issued checks and decide exceptions.", evidence)
    return Control("customer_sod", title, PASS, "No customer person can both submit issued checks and decide exceptions.", evidence)


def platform_staff(session: Session, tenant_id: uuid.UUID) -> Control:
    """The installation's platform staff (scripts/platform_staff.py), each with a second factor."""
    from openclear.domain.user import User
    from openclear.domain.webauthn_credential import WebauthnCredential

    title = "Platform staff use a second factor"
    staff = session.execute(select(User).where(User.platform_role.is_not(None), User.is_active.is_(True))).scalars().all()
    with_keys = set(session.execute(select(WebauthnCredential.user_id).where(
        WebauthnCredential.user_id.in_([u.id for u in staff]))).scalars()) if staff else set()
    without = sorted(u.email for u in staff if u.id not in with_keys)
    evidence = {"platform_staff": len(staff), "without_a_security_key": without}
    if without:
        return Control("platform_staff", title, WARN, f"{len(without)} of {len(staff)} platform staff have no security key or passkey.", evidence)
    return Control("platform_staff", title, PASS, f"All {len(staff)} platform staff have a security key or passkey.", evidence)


# --- Integrations and sign-on ------------------------------------------------------------------


def customer_api_client_secrets(session: Session, tenant_id: uuid.UUID) -> Control:
    from openclear.domain.api_client import ApiClient

    cutoff = _now() - timedelta(days=SECRET_MAX_AGE_DAYS)
    clients = session.execute(select(ApiClient).where(ApiClient.tenant_id == tenant_id, ApiClient.managed_by == "customer",
                                                      ApiClient.revoked_at.is_(None))).scalars().all()
    old = sorted(c.name for c in clients if c.token_auth_method != "private_key_jwt"
                 and _aware(c.secret_rotated_at or c.created_at) < cutoff)
    evidence = {"customer_clients": len(clients), "key_pairs": sum(c.token_auth_method == "private_key_jwt" for c in clients),
                "older_than_a_year": old}
    title = "Customers' integration secrets rotated"
    if old:
        return Control("customer_api_client_secrets", title, WARN,
                       f"{_plural(len(old), 'customer API client')} haven't rotated their secret in over a year: {', '.join(old[:10])}.",
                       evidence)
    return Control("customer_api_client_secrets", title, PASS,
                   f"All {len(clients)} customers' API clients rotated within a year or use a key pair.", evidence)


def customer_sso(session: Session, tenant_id: uuid.UUID) -> Control:
    from openclear.domain.customer import Customer
    from openclear.domain.sso_connection import SsoConnection

    connections = session.execute(select(SsoConnection, Customer.name).join(Customer, Customer.id == SsoConnection.customer_id)
                                  .where(SsoConnection.tenant_id == tenant_id, SsoConnection.managed_by == "customer")).all()
    active = sorted({name for c, name in connections if c.is_active})
    evidence = {"customers_with_their_own_sign_on": active, "connections": len(connections)}
    return Control("customer_sso", "Customers' own sign-on", PASS,
                   (f"{_plural(len(active), 'customer')} sign their people in through their own identity provider."
                    if active else "No customer runs its own sign-on; the bank sets how customers sign in."), evidence)


def scim_sync(session: Session, tenant_id: uuid.UUID) -> Control:
    from openclear.domain.sso_connection import ScimUser, SsoConnection
    from openclear.domain.tenant_membership import TenantMembership

    title = "Directories in step"
    rows = session.execute(select(SsoConnection.id, SsoConnection.display_name, func.max(ScimUser.updated_at))
                           .join(ScimUser, ScimUser.connection_id == SsoConnection.id)
                           .where(SsoConnection.tenant_id == tenant_id).group_by(SsoConnection.id, SsoConnection.display_name)).all()
    if not rows:
        return Control("scim_sync", title, PASS, "No directory provisions people over SCIM.", {"directories": 0})
    stale_active = session.execute(select(func.count()).select_from(ScimUser).join(
        TenantMembership, and_(TenantMembership.user_id == ScimUser.user_id, TenantMembership.tenant_id == ScimUser.tenant_id))
        .where(ScimUser.tenant_id == tenant_id, ScimUser.active.is_(False), TenantMembership.is_active.is_(True))).scalar_one()
    evidence = {"directories": [{"connection": name, "last_change": _aware(last).isoformat() if last else None} for _id, name, last in rows],
                "disabled_in_the_directory_but_active": stale_active}
    if stale_active:
        return Control("scim_sync", title, FAIL,
                       f"{_plural(stale_active, 'person')} the directory disabled still have an active membership.", evidence)
    return Control("scim_sync", title, PASS, f"{_plural(len(rows), 'directory')} in step; no one it disabled is still active.", evidence)


def integration_tls(session: Session, tenant_id: uuid.UUID) -> Control:
    from openclear.domain.security_event_destination import SecurityEventDestination
    from openclear.domain.webhook import WebhookEndpoint as Webhook

    title = "Integrations use HTTPS"
    hooks = session.execute(select(Webhook.url).where(Webhook.tenant_id == tenant_id)).scalars().all()
    destinations = session.execute(select(SecurityEventDestination).where(SecurityEventDestination.tenant_id == tenant_id,
                                                                          SecurityEventDestination.enabled.is_(True))).scalars().all()
    plain = [u for u in hooks if not u.lower().startswith("https://")]
    plain += [d.url for d in destinations if d.url and not d.url.lower().startswith("https://")]
    plain += [f"syslog://{d.host}" for d in destinations if d.kind == "syslog" and d.transport not in ("tls", None)]
    evidence = {"webhooks": len(hooks), "event_destinations": len(destinations), "not_encrypted": plain[:LIST_LIMIT]}
    if plain:
        return Control("integration_tls", title, FAIL, f"{_plural(len(plain), 'integration')} send data unencrypted: {', '.join(plain[:5])}.",
                       evidence)
    return Control("integration_tls", title, PASS, f"All {len(hooks) + len(destinations)} webhooks and event destinations are encrypted.",
                   evidence)


def model_pooling_acknowledged(session: Session, tenant_id: uuid.UUID) -> Control:
    from openclear.domain.tenant import Tenant

    tenant = session.get(Tenant, tenant_id)
    source = getattr(tenant.ml_model_source, "value", tenant.ml_model_source)
    evidence = {"model": source, "acknowledged_at": _aware(tenant.ml_shared_consent_at).isoformat() if tenant.ml_shared_consent_at else None}
    title = "Model data pooling acknowledged"
    if source == "shared" and tenant.ml_shared_consent_at is None:
        return Control("model_pooling_acknowledged", title, WARN,
                       "You score with the shared fraud model, which learns from every participating bank's decisions, and nobody has "
                       "acknowledged its data-pooling disclosure. Do it under Settings → Fraud model.", evidence)
    if source == "shared":
        return Control("model_pooling_acknowledged", title, PASS, "On the shared model, with its disclosure acknowledged.", evidence)
    return Control("model_pooling_acknowledged", title, PASS, "On your own model: your decisions aren't pooled.", evidence)


# --- Positive pay operations -------------------------------------------------------------------


def default_dispositions(session: Session, tenant_id: uuid.UUID) -> Control:
    """Every customer with accounts says what happens to an exception nobody decides in time."""
    from openclear.domain.account import Account
    from openclear.domain.customer import Customer
    from openclear.domain.customer_disposition_setting import CustomerDispositionSetting, DispositionMode

    customers = session.execute(select(Customer).where(Customer.tenant_id == tenant_id, Customer.is_active.is_(True),
                                                       Customer.id.in_(select(Account.customer_id).where(Account.tenant_id == tenant_id)))
                                ).scalars().all()
    set_up = set(session.execute(select(CustomerDispositionSetting.customer_id).where(
        CustomerDispositionSetting.tenant_id == tenant_id, CustomerDispositionSetting.mode != DispositionMode.NONE)).scalars())
    missing = sorted(c.name for c in customers if c.id not in set_up)
    evidence = {"customers_with_accounts": len(customers), "without_a_default": missing[:LIST_LIMIT]}
    title = "Default dispositions set"
    if missing:
        return Control("default_dispositions", title, WARN,
                       f"{_plural(len(missing), 'customer')} have no default for undecided exceptions, so a missed cutoff leaves them "
                       f"open: {', '.join(missing[:10])}.", evidence)
    return Control("default_dispositions", title, PASS, f"All {len(customers)} customers with accounts have a default disposition.", evidence)


def rejected_rows(session: Session, tenant_id: uuid.UUID) -> Control:
    """Upload rows rejected over a business day ago (in the last 30 days), still not corrected."""
    from openclear.domain.bulk_upload_file import BulkUploadCorrection, BulkUploadFile, BulkUploadRowResult

    now = _now()
    corrected = select(BulkUploadCorrection.id).where(BulkUploadCorrection.bulk_upload_file_id == BulkUploadRowResult.bulk_upload_file_id,
                                                      BulkUploadCorrection.row_index == BulkUploadRowResult.row_index,
                                                      BulkUploadCorrection.success.is_(True))
    rows = session.execute(select(BulkUploadFile.original_filename, func.count()).select_from(BulkUploadRowResult).join(
        BulkUploadFile, BulkUploadFile.id == BulkUploadRowResult.bulk_upload_file_id)
        .where(BulkUploadRowResult.tenant_id == tenant_id, BulkUploadRowResult.success.is_(False),
               BulkUploadFile.uploaded_at <= now - timedelta(days=1), BulkUploadFile.uploaded_at >= now - timedelta(days=30),
               ~corrected.exists()).group_by(BulkUploadFile.original_filename)).all()
    total = sum(count for _name, count in rows)
    evidence = {"uncorrected_rows": total, "files": [{"file": name, "rows": count} for name, count in rows][:LIST_LIMIT]}
    title = "Rejected rows corrected"
    if total:
        return Control("rejected_rows", title, WARN,
                       f"{_plural(total, 'rejected row')} in {_plural(len(rows), 'upload')} have waited over a day without being corrected.",
                       evidence)
    return Control("rejected_rows", title, PASS, "Every row rejected in the last 30 days was corrected within a day.", evidence)


def stale_ach_authorizations(session: Session, tenant_id: uuid.UUID) -> Control:
    from openclear.domain.ach_authorization_rule import AchAuthorizationRule, AchAuthorizationStatus
    from openclear.domain.ach_transaction import AchTransaction

    today = date.today()
    year_ago = _now() - timedelta(days=365)
    active = session.execute(select(AchAuthorizationRule).where(AchAuthorizationRule.tenant_id == tenant_id,
                                                                AchAuthorizationRule.status == AchAuthorizationStatus.ACTIVE)).scalars().all()
    expired = [a for a in active if a.expiration_date is not None and a.expiration_date < today]
    used = set(session.execute(select(AchTransaction.account_id, AchTransaction.originator_id).where(
        AchTransaction.tenant_id == tenant_id, AchTransaction.effective_date >= year_ago.date())).all())
    unused = [a for a in active if a not in expired and _aware(a.created_at) < year_ago and (a.account_id, a.originator_id) not in used]
    evidence = {"active": len(active), "past_their_end_date": len(expired), "unused_for_a_year": len(unused),
                "examples": sorted(f"{a.originator_name} ({a.originator_id})" for a in (expired + unused))[:LIST_LIMIT]}
    title = "ACH authorizations current"
    if expired or unused:
        return Control("stale_ach_authorizations", title, WARN,
                       f"{_plural(len(expired), 'authorization')} past their end date and {len(unused)} unused for a year are still active.",
                       evidence)
    return Control("stale_ach_authorizations", title, PASS, f"All {len(active)} active ACH authorizations are current and in use.", evidence)


def wsud_signed(session: Session, tenant_id: uuid.UUID) -> Control:
    """Consumer debits returned 7 to 60 days ago, each with a signed Written Statement of
    Unauthorized Debit (the customer signs after the return, so a week's grace)."""
    from openclear.domain.ach_transaction import AchSettlementStatus, AchTransaction
    from openclear.domain.wsud_statement_transaction import WsudStatementTransaction
    from openclear.services.wsud_service import CONSUMER_SEC_CODES

    now = _now()
    returned = select(AchTransaction.id).where(
        AchTransaction.tenant_id == tenant_id, AchTransaction.settlement_status == AchSettlementStatus.RETURNED,
        AchTransaction.sec_code.in_(CONSUMER_SEC_CODES), AchTransaction.updated_at <= now - timedelta(days=7),
        AchTransaction.updated_at >= now - timedelta(days=60))
    total = session.execute(select(func.count()).select_from(returned.subquery())).scalar_one()
    signed = session.execute(select(func.count(func.distinct(WsudStatementTransaction.ach_transaction_id))).where(
        WsudStatementTransaction.ach_transaction_id.in_(returned))).scalar_one()
    evidence = {"consumer_debits_returned": total, "with_a_signed_statement": signed}
    title = "Unauthorized debits signed for"
    if total > signed:
        return Control("wsud_signed", title, WARN,
                       f"{total - signed} of {total} consumer debits returned over a week ago have no signed Written Statement of "
                       "Unauthorized Debit.", evidence)
    return Control("wsud_signed", title, PASS, f"All {total} consumer debits returned 7 to 60 days ago have a signed statement.", evidence)


def evidence_export(session: Session, tenant_id: uuid.UUID) -> Control:
    from openclear.domain.audit_log_entry import AuditLogEntry

    last = session.execute(select(func.max(AuditLogEntry.occurred_at)).where(AuditLogEntry.tenant_id == tenant_id,
                                                                            AuditLogEntry.action == "audit.export")).scalar_one()
    last = _aware(last)
    evidence = {"last_export": last.isoformat() if last else None}
    title = "Evidence exported this quarter"
    if last is None or last < _now() - timedelta(days=92):
        return Control("evidence_export", title, WARN,
                       "No evidence export in the last quarter. Take one from Controls → Export evidence and keep it.", evidence)
    return Control("evidence_export", title, PASS, f"Last exported {last:%Y-%m-%d}.", evidence)


# --- The installation --------------------------------------------------------------------------


def scheduled_jobs(session: Session, tenant_id: uuid.UUID) -> Control:
    """Each scheduled job's last run against its schedule (workers/scheduler.py)."""
    from openclear.domain.scheduled_job_run import ScheduledJobRun
    from openclear.workers.scheduler import expected_jobs

    title = "Scheduled jobs running"
    expected = expected_jobs()
    if not expected:
        return Control("scheduled_jobs", title, PASS, "No scheduled jobs are configured on this installation.", {"jobs": {}})
    runs = {r.job_id: r for r in session.execute(select(ScheduledJobRun)).scalars()}
    now, late, failing, report = _now(), [], [], {}
    for job_id, every in expected.items():
        run = runs.get(job_id)
        last = _aware(run.last_finished_at) if run else None
        report[job_id] = {"last_finished": last.isoformat() if last else None, "ok": run.last_ok if run else None}
        if last is None or last < now - every * SCHEDULE_SLACK - timedelta(minutes=5):
            late.append(job_id)
        elif run.last_ok is False:
            failing.append(job_id)
    evidence = {"jobs": report, "late": late, "failing": failing}
    if late or failing:
        return Control("scheduled_jobs", title, FAIL if late else WARN,
                       "; ".join(filter(None, [f"late: {', '.join(late)}" if late else "", f"last run failed: {', '.join(failing)}"
                                               if failing else ""])).capitalize() + ".", evidence)
    return Control("scheduled_jobs", title, PASS, f"All {len(expected)} scheduled jobs ran on time.", evidence)


def transport_security(session: Session, tenant_id: uuid.UUID) -> Control:
    """Whether the installation is served over HTTPS (its configured origin, which also decides
    Secure cookies and HSTS: web/security.py), and HSTS's lifetime."""
    from openclear.web.security import is_https_deployment

    settings = get_settings()
    https = is_https_deployment()
    evidence = {"origin": settings.webauthn_origin, "https": https, "hsts_max_age_seconds": settings.hsts_max_age_seconds,
                "environment": settings.environment}
    title = "HTTPS with HSTS"
    if settings.environment != "production":
        return Control("transport_security", title, WARN,
                       f"Running as {settings.environment!r}: HTTPS and HSTS are a production installation's.", evidence)
    if not https:
        return Control("transport_security", title, FAIL, f"The installation's origin ({settings.webauthn_origin}) isn't HTTPS.", evidence)
    if not settings.hsts_max_age_seconds:
        return Control("transport_security", title, WARN, "Requests arrive over HTTPS, but HSTS is off.", evidence)
    return Control("transport_security", title, PASS, "Requests arrive over HTTPS, and browsers are told to use only HTTPS (HSTS).", evidence)


# --- Population checks: every item in a period -------------------------------------------------


def dual_control(session: Session, tenant_id: uuid.UUID, start: datetime, end: datetime) -> Control:
    from openclear.domain.decision import Decision, DecisionSource
    from openclear.domain.tenant import Tenant

    title = "Dual control on every decision"
    required = session.get(Tenant, tenant_id).require_dual_control
    decisions = session.execute(select(Decision).where(Decision.tenant_id == tenant_id, Decision.source == DecisionSource.HUMAN,
                                                       Decision.decided_at >= start, Decision.decided_at < end)).scalars().all()
    single = [d for d in decisions if d.submitted_by_user_id is None or d.submitted_by_user_id == d.decided_by_user_id]
    evidence = {"dual_control_required": required, "decisions": len(decisions), "without_a_second_person": len(single),
                "examples": [str(d.exception_item_id) for d in single[:LIST_LIMIT]], "period": [start.isoformat(), end.isoformat()]}
    if not required:
        return Control("dual_control", title, WARN, f"Dual control is off; {len(decisions)} decisions in the period, "
                                                    f"{len(single)} of them by one person.", evidence)
    if single:
        return Control("dual_control", title, FAIL, f"{len(single)} of {len(decisions)} decisions in the period had no second person.", evidence)
    return Control("dual_control", title, PASS, f"All {len(decisions)} decisions in the period were recommended and approved by different people.",
                   evidence)


def two_person_changes(session: Session, tenant_id: uuid.UUID, start: datetime, end: datetime) -> Control:
    from openclear.domain.change_request import ChangeRequest

    title = "Settings changes approved by a second person"
    decided = session.execute(select(ChangeRequest).where(ChangeRequest.tenant_id == tenant_id,
                                                          ChangeRequest.status.in_(("approved", "applied_alone")),
                                                          ChangeRequest.decided_at >= start, ChangeRequest.decided_at < end)).scalars().all()
    approved = [c for c in decided if c.status == "approved"]
    alone = [c for c in decided if c.status == "applied_alone"]
    same = [c for c in approved if c.decided_by_user_id == c.requested_by_user_id]
    evidence = {"approved_changes": len(approved), "approved_by_their_requester": len(same), "applied_without_a_second_person": len(alone),
                "kinds": sorted({c.kind for c in decided}), "period": [start.isoformat(), end.isoformat()]}
    if same:
        return Control("two_person_changes", title, FAIL, f"{len(same)} of {len(approved)} changes were approved by the person who asked.", evidence)
    if alone:
        return Control("two_person_changes", title, WARN, f"{len(alone)} changes took effect after their 24 hours with nobody else to approve "
                                                          f"them: {', '.join(c.summary for c in alone[:5])}.", evidence)
    return Control("two_person_changes", title, PASS, f"All {len(approved)} changes approved in the period had a second person.", evidence)


def change_impact_replay(session: Session, tenant_id: uuid.UUID, start: datetime, end: datetime) -> Control:
    from openclear.domain.change_request import ChangeRequest
    from openclear.domain.control_run import ControlRun
    from openclear.services.mrm_service import REPLAY

    title = "Changes replayed before approval"
    approved = session.execute(select(ChangeRequest).where(ChangeRequest.tenant_id == tenant_id, ChangeRequest.status == "approved",
                                                           ChangeRequest.kind.in_(("matching_rule.set", "ml_model.activate")),  # how items are judged
                                                           ChangeRequest.decided_at >= start, ChangeRequest.decided_at < end)).scalars().all()
    replays = [_aware(r) for r in session.execute(select(ControlRun.started_at).where(
        ControlRun.tenant_id == tenant_id, ControlRun.kind == REPLAY, ControlRun.started_at >= start - timedelta(days=30),
        ControlRun.started_at < end)).scalars()]
    without = [c for c in approved if not any(_aware(c.decided_at) - timedelta(days=30) <= r <= _aware(c.decided_at) for r in replays)]
    evidence = {"approved_changes": len(approved), "without_a_replay_in_the_30_days_before": len(without)}
    if without:
        return Control("change_impact_replay", title, WARN,
                       f"{len(without)} of {len(approved)} approved changes had no replay in the 30 days before.", evidence)
    return Control("change_impact_replay", title, PASS, f"All {len(approved)} approved changes had a replay beforehand.", evidence)


def support_access_review(session: Session, tenant_id: uuid.UUID, start: datetime, end: datetime) -> Control:
    from openclear.domain.support_access_grant import SupportAccessGrant

    title = "Support access granted with care"
    grants = session.execute(select(SupportAccessGrant).where(SupportAccessGrant.tenant_id == tenant_id,
                                                              SupportAccessGrant.granted_at >= start, SupportAccessGrant.granted_at < end)
                             ).scalars().all()
    long_ones = [g for g in grants if _aware(g.expires_at) - _aware(g.granted_at) > timedelta(days=30)]
    no_reason = [g for g in grants if not (g.reason or "").strip()]
    evidence = {"grants": len(grants), "longer_than_30_days": len(long_ones), "without_a_reason": len(no_reason),
                "with_write_access": sum(g.write_access for g in grants)}
    if no_reason or long_ones:
        return Control("support_access_review", title, WARN,
                       f"Of {len(grants)} support grants in the period, {len(no_reason)} had no reason and {len(long_ones)} lasted over 30 days.",
                       evidence)
    return Control("support_access_review", title, PASS, f"All {len(grants)} support grants in the period named a reason and an end.", evidence)


def customer_decision_timeliness(session: Session, tenant_id: uuid.UUID, start: datetime, end: datetime) -> Control:
    """Customers' exceptions whose deadline fell in the period: decided by a person in time."""
    from openclear.domain.decision import Decision, DecisionSource
    from openclear.domain.exception_item import ExceptionItem

    title = "Customers decide in time"
    due = session.execute(select(ExceptionItem.id, ExceptionItem.decision_deadline).where(
        ExceptionItem.tenant_id == tenant_id, ExceptionItem.customer_id.is_not(None), ExceptionItem.decision_deadline >= start,
        ExceptionItem.decision_deadline < end)).all()
    decided = {row.exception_item_id: (row.decided_at, row.source) for row in session.execute(
        select(Decision.exception_item_id, Decision.decided_at, Decision.source).where(
            Decision.exception_item_id.in_([i for i, _d in due]))).all()} if due else {}
    late = [i for i, deadline in due if i not in decided or decided[i][1] != DecisionSource.HUMAN
            or _aware(decided[i][0]) > _aware(deadline)]
    evidence = {"due_in_the_period": len(due), "missed_or_defaulted": len(late)}
    if late:
        return Control("customer_decision_timeliness", title, WARN,
                       f"{len(late)} of {len(due)} customer exceptions due in the period weren't decided by a person in time.", evidence)
    return Control("customer_decision_timeliness", title, PASS, f"All {len(due)} customer exceptions due in the period were decided in time.",
                   evidence)


# --- Deviations themselves ---------------------------------------------------------------------


def control_response(session: Session, tenant_id: uuid.UUID) -> Control:
    from openclear.domain.control_result import ControlDeviation
    from openclear.domain.tenant import Tenant

    grace = timedelta(hours=session.get(Tenant, tenant_id).control_grace_hours)
    now = _now()
    open_ = session.execute(select(ControlDeviation).where(ControlDeviation.tenant_id == tenant_id,
                                                           ControlDeviation.closed_at.is_(None))).scalars().all()
    since = now - timedelta(days=30)
    closed = session.execute(select(ControlDeviation).where(ControlDeviation.tenant_id == tenant_id,
                                                            ControlDeviation.closed_at >= since)).scalars().all()
    overdue = [d for d in open_ if _aware(d.opened_at) < now - grace]
    long_closed = [d for d in closed if _aware(d.closed_at) - _aware(d.opened_at) > grace]
    unanswered = [d for d in open_ + closed if not d.response]
    evidence = {"open": len(open_), "open_beyond_grace": [d.check_key for d in overdue], "closed_last_30_days": len(closed),
                "closed_after_the_grace_period": len(long_closed), "without_a_response": len(unanswered),
                "grace_hours": int(grace.total_seconds() // 3600)}
    title = "Deviations dealt with"
    if overdue:
        return Control("control_response", title, FAIL,
                       f"{_plural(len(overdue), 'deviation')} open longer than the {evidence['grace_hours']}-hour grace period: "
                       f"{', '.join(sorted({d.check_key for d in overdue}))}.", evidence)
    if open_ or unanswered:
        return Control("control_response", title, WARN,
                       f"{len(open_)} open, and {len(unanswered)} without a recorded cause and response.", evidence)
    return Control("control_response", title, PASS, "No open deviations, and every recent one has its cause and response.", evidence)


# --- CT3: access reviews and the data the last checks need --------------------------------------

REVIEW_EVERY_DAYS = 92  # a quarter
KEY_FILES = {"jwt_private_key_path": "Session token signing", "file_signing_private_key_path": "File and evidence signing",
             "audit_log_signing_private_key_path": "Action log signing", "wsud_signing_private_key_path": "Unauthorized-debit statement signing"}


def _last_review(session: Session, tenant_id: uuid.UUID, customer_id):
    from openclear.domain.access_review import AccessReview

    stmt = select(AccessReview).where(AccessReview.tenant_id == tenant_id,
                                      AccessReview.customer_id == customer_id if customer_id else AccessReview.customer_id.is_(None))
    completed = session.execute(stmt.where(AccessReview.completed_at.is_not(None)).order_by(AccessReview.completed_at.desc()).limit(1)).scalar_one_or_none()
    still_open = session.execute(stmt.where(AccessReview.completed_at.is_(None))).scalar_one_or_none()
    return completed, still_open


def access_review(session: Session, tenant_id: uuid.UUID) -> Control:
    title = "Access reviewed this quarter"
    completed, still_open = _last_review(session, tenant_id, None)
    now = _now()
    evidence = {"last_completed": _aware(completed.completed_at).isoformat() if completed else None,
                "decisions_sha256": completed.decisions_sha256 if completed else None, "people_reviewed": completed.item_count if completed else 0,
                "open_since": _aware(still_open.started_at).isoformat() if still_open else None}
    if completed is not None and _aware(completed.completed_at) >= now - timedelta(days=REVIEW_EVERY_DAYS):
        return Control("access_review", title, PASS, f"Every membership reviewed on {completed.completed_at:%Y-%m-%d} ({completed.item_count}).",
                       evidence)
    if still_open is not None:
        return Control("access_review", title, WARN, f"A review started {still_open.started_at:%Y-%m-%d} isn't finished.", evidence)
    return Control("access_review", title, WARN, "No access review completed in the last quarter. Start one from Settings → Access reviews.",
                   evidence)


def customer_access_review(session: Session, tenant_id: uuid.UUID) -> Control:
    from openclear.domain.customer import Customer

    title = "Customers reviewed their access"
    customers = session.execute(select(Customer).where(Customer.tenant_id == tenant_id, Customer.is_active.is_(True),
                                                       Customer.self_manages_users.is_(True))).scalars().all()
    cutoff = _now() - timedelta(days=REVIEW_EVERY_DAYS)
    overdue = sorted(c.name for c in customers if not ((done := _last_review(session, tenant_id, c.id)[0]) and _aware(done.completed_at) >= cutoff))
    evidence = {"self_managing_customers": len(customers), "without_a_review_this_quarter": overdue[:LIST_LIMIT]}
    if overdue:
        return Control("customer_access_review", title, WARN,
                       f"{_plural(len(overdue), 'self-managing customer')} haven't reviewed their people this quarter: {', '.join(overdue[:10])}.",
                       evidence)
    return Control("customer_access_review", title, PASS,
                   (f"All {len(customers)} self-managing customers reviewed their people this quarter." if customers
                    else "No customer manages its own people; the bank's review covers them."), evidence)


def file_reconciliation(session: Session, tenant_id: uuid.UUID) -> Control:
    """Presented-items files from the last 30 days: what each declared against what loaded,
    counting rows corrected since."""
    from openclear.domain.bulk_upload_file import BulkUploadCorrection, BulkUploadFile

    now = _now()
    files = session.execute(select(BulkUploadFile).where(BulkUploadFile.tenant_id == tenant_id, BulkUploadFile.control_item_count.is_not(None),
                                                         BulkUploadFile.uploaded_at >= now - timedelta(days=30))).scalars().all()
    corrected = dict(session.execute(select(BulkUploadCorrection.bulk_upload_file_id, func.count()).where(
        BulkUploadCorrection.tenant_id == tenant_id, BulkUploadCorrection.success.is_(True),
        BulkUploadCorrection.bulk_upload_file_id.in_([f.id for f in files])).group_by(BulkUploadCorrection.bulk_upload_file_id)).all()) if files else {}
    gaps = [(f, f.control_item_count - (f.loaded_item_count or 0) - corrected.get(f.id, 0)) for f in files]
    open_gaps = [(f, gap) for f, gap in gaps if gap > 0 and _aware(f.uploaded_at) <= now - timedelta(days=1)]
    evidence = {"files": len(files), "declared_items": sum(f.control_item_count for f in files),
                "loaded_items": sum((f.loaded_item_count or 0) for f in files) + sum(corrected.values()),
                "files_not_reconciled": [{"file": f.original_filename, "items_missing": gap,
                                          "declared_total": str(f.control_total), "loaded_total": str(f.loaded_total)} for f, gap in open_gaps][:LIST_LIMIT]}
    title = "Presented-items files reconciled"
    if open_gaps:
        return Control("file_reconciliation", title, WARN,
                       f"{_plural(len(open_gaps), 'file')} loaded fewer items than they declared, and the difference hasn't been corrected.",
                       evidence)
    return Control("file_reconciliation", title, PASS,
                   f"All {len(files)} X9.37 and NACHA files in the last 30 days loaded what they declared." if files
                   else "No X9.37 or NACHA presented-items files in the last 30 days.", evidence)


def decision_delivery(session: Session, tenant_id: uuid.UUID) -> Control:
    from openclear.domain.decision_outbox import DecisionBatch, DecisionOutbox

    title = "Decision files delivered and acknowledged"
    outbox = session.execute(select(DecisionOutbox).where(DecisionOutbox.tenant_id == tenant_id)).scalar_one_or_none()
    now = _now()
    if outbox is None or not outbox.enabled:
        return Control("decision_delivery", title, PASS, "Decision files are off: the core reads decisions from the API's feed.",
                       {"decision_files": False})
    waiting = session.execute(select(DecisionBatch).where(DecisionBatch.tenant_id == tenant_id, DecisionBatch.acknowledged_at.is_(None),
                                                          DecisionBatch.created_at <= now - timedelta(hours=24))).scalars().all()
    evidence = {"decision_files": True, "unacknowledged_over_a_day": len(waiting),
                "oldest": _aware(min(b.created_at for b in waiting)).isoformat() if waiting else None}
    if waiting:
        return Control("decision_delivery", title, WARN,
                       f"{_plural(len(waiting), 'decision file')} haven't been acknowledged by the core after a day.", evidence)
    return Control("decision_delivery", title, PASS, "Every decision file older than a day was acknowledged by the core.", evidence)


def outside_services(session: Session, tenant_id: uuid.UUID) -> Control:
    from openclear.services import outside_services as services

    title = "Outside services reviewed"
    used = services.in_use(session, tenant_id)
    cutoff = _now() - timedelta(days=services.REVIEW_EVERY_DAYS)
    stale = [s.label for s in used if s.reviewed_at is None or _aware(s.reviewed_at) < cutoff]
    evidence = {"services": [{"service": s.label, "detail": s.detail, "reviewed_at": _aware(s.reviewed_at).isoformat() if s.reviewed_at else None}
                             for s in used], "not_reviewed_this_year": stale}
    if stale:
        return Control("outside_services", title, WARN,
                       f"{_plural(len(stale), 'outside service')} not reviewed in the last year: {', '.join(stale[:5])}.", evidence)
    return Control("outside_services", title, PASS,
                   f"All {len(used)} outside services reviewed within the year." if used else "OpenClear uses no outside services for you.",
                   evidence)


def password_policy(session: Session, tenant_id: uuid.UUID) -> Control:
    from openclear.domain.tenant import Tenant

    tenant = session.get(Tenant, tenant_id)
    evidence = {"your_standard_minimum": tenant.standard_password_min_length, "minimum_in_force": tenant.password_min_length,
                "password_sign_in": tenant.password_login_enabled}
    title = "Password policy meets your standard"
    if not tenant.password_login_enabled:
        return Control("password_policy", title, PASS, "Password sign-in is off: your staff sign in through your identity provider.", evidence)
    if tenant.standard_password_min_length is None:
        return Control("password_policy", title, WARN,
                       "Record your own password standard (Settings → Your standards) so this can check OpenClear against it.", evidence)
    if tenant.password_min_length < tenant.standard_password_min_length:
        return Control("password_policy", title, FAIL,
                       f"Passwords may be {tenant.password_min_length} characters, below your standard of {tenant.standard_password_min_length}.",
                       evidence)
    return Control("password_policy", title, PASS,
                   f"Passwords need at least {tenant.password_min_length} characters; your standard is {tenant.standard_password_min_length}.",
                   evidence)


def key_age(session: Session, tenant_id: uuid.UUID) -> Control:
    """The signing keys' ages (their files' last change) against the rotation period the bank records."""
    import os

    from openclear.domain.tenant import Tenant

    settings = get_settings()
    period = session.get(Tenant, tenant_id).key_rotation_days
    now = _now()
    keys = {}
    for setting, label in KEY_FILES.items():
        try:
            changed = datetime.fromtimestamp(os.path.getmtime(getattr(settings, setting)), tz=timezone.utc)
        except OSError:
            continue
        keys[label] = (now - changed).days
    evidence = {"rotation_period_days": period, "key_age_days": keys}
    title = "Keys rotated on schedule"
    if period is None:
        return Control("key_age", title, WARN, "Record how often your keys are rotated (Settings → Your standards) so this can check their age.",
                       evidence)
    old = sorted(label for label, days in keys.items() if days > period)
    if old:
        return Control("key_age", title, WARN, f"Older than your {period}-day rotation period: {', '.join(old)}.", evidence)
    return Control("key_age", title, PASS, f"All {len(keys)} signing keys are younger than your {period}-day rotation period.", evidence)


def software_currency(session: Session, tenant_id: uuid.UUID) -> Control:
    """How long a newer OpenClear release than the one running has been out (services/release_feed.py):
    PASS within `software_currency_warn_days`, WARN after, FAIL after `software_currency_fail_days`."""
    from importlib.metadata import PackageNotFoundError, version

    from openclear.services import release_feed

    settings = get_settings()
    title = "Software kept up to date"
    try:
        running_text = version("openclear")
    except PackageNotFoundError:
        running_text = "unknown"
    running = release_feed.parse_version(running_text)
    evidence: dict = {"running": running_text, "feed": settings.release_feed_url or None, "warn_days": settings.software_currency_warn_days,
                      "fail_days": settings.software_currency_fail_days}
    try:
        published = release_feed.releases()
    except release_feed.FeedError as exc:
        evidence["error"] = str(exc)
        return Control("software_currency", title, WARN, f"Couldn't read the release feed, so OpenClear can't tell whether {running_text} "
                                                         f"is current: {exc}", evidence)
    if published is None:
        return Control("software_currency", title, WARN, f"Running {running_text}. No release feed is set (OPENCLEAR_RELEASE_FEED_URL), "
                                                         "so OpenClear can't tell whether it's current.", evidence)
    evidence["latest"] = published[-1].tag
    if running is None:
        return Control("software_currency", title, WARN, f"Running {running_text}, which isn't a published release.", evidence)
    newer = [r for r in published if r.version > running]
    evidence["newer"] = [{"tag": r.tag, "published_at": r.published_at.isoformat()} for r in newer]
    if not newer:
        return Control("software_currency", title, PASS, f"Running {running_text}, the latest release.", evidence)
    days = (_now() - newer[0].published_at).days
    evidence["days_since_first_newer"] = days
    behind = f"Running {running_text}; {len(newer)} newer release{'s' if len(newer) != 1 else ''}, the first ({newer[0].tag}) out {days} days"
    if days > settings.software_currency_fail_days:
        return Control("software_currency", title, FAIL, f"{behind}, more than {settings.software_currency_fail_days}.", evidence)
    if days > settings.software_currency_warn_days:
        return Control("software_currency", title, WARN, f"{behind}, more than {settings.software_currency_warn_days}.", evidence)
    return Control("software_currency", title, PASS, f"{behind}, within {settings.software_currency_warn_days}. Latest: {newer[-1].tag}.", evidence)


def installation_settings(session: Session, tenant_id: uuid.UUID) -> Control:
    """The installation's security-relevant settings, against what a platform operator last
    acknowledged (services/installation_settings.py; DUAL_CONTROL_PLAN.md DC-D14)."""
    from openclear.services import installation_settings as watched

    title = "Installation settings unchanged"
    names, row = watched.changed(session)
    evidence = {"watched": list(watched.WATCHED), "changed": names,
                "acknowledged_at": row.acknowledged_at.isoformat() if row and row.acknowledged_at else None}
    if names:
        return Control("installation_settings", title, WARN,
                       f"Changed since the platform's operators last acknowledged them: {', '.join(names)}. An operator acknowledges "
                       "them in the operator view once the change is confirmed as intended.", evidence)
    return Control("installation_settings", title, PASS, f"The {len(watched.WATCHED)} watched settings are as last acknowledged.", evidence)


def provider_log(session: Session, tenant_id: uuid.UUID) -> Control:
    """The service provider's own action log intact (DUAL_CONTROL_PLAN.md DC6), from its last
    verification. A bank's session can't read that log, so it's verified daily from a session bound
    to no bank, and on Platform → Changes; this reads the result."""
    from openclear.services import long_runs, platform_log_service

    title = "Service provider's action log intact"
    run = platform_log_service.latest_verification(session)
    if run is None:
        return Control("provider_log", title, WARN, "Not verified yet. It's verified daily with the action logs, and when an operator opens "
                                                    "Platform → Changes.", {"verified_at": None})
    evidence = {"verified_at": _aware(run.verified_at).isoformat(), "entries_checked": run.checked, "broken_at_sequence": run.broken_at_sequence}
    if not run.intact:
        return Control("provider_log", title, FAIL, f"Entry {run.broken_at_sequence} of the provider's action log doesn't verify: an entry was "
                                                    "changed or removed outside OpenClear.", evidence)
    if _aware(run.verified_at) < datetime.now(timezone.utc) - long_runs.VERIFICATION_MAX_AGE:
        return Control("provider_log", title, WARN, f"The last verification passed ({run.checked} entries) but is more than a day old: "
                                                    "is the daily schedule on?", evidence)
    return Control("provider_log", title, PASS, f"All {run.checked} entries verified.", evidence)


def second_approver(session: Session, tenant_id: uuid.UUID) -> Control:
    """Whether each second-person rule the bank has can be met (DUAL_CONTROL_PLAN.md DC-D16, DC-D17):
    for each switch that's on, and for turning protections off, at least two people who could
    approve; and whether sensitive settings are protected at a bank that has the people to."""
    from openclear.domain.tenant import Tenant
    from openclear.services import change_kinds, change_request_service

    tenant = session.get(Tenant, tenant_id)
    title = "Someone else can approve"

    def enough(permission: str) -> bool:
        return len(change_request_service.approvers(session, tenant_id, None, permission, excluding=None)) >= 2

    needed = [("turning a protection off", "tenant:manage")]
    if tenant.require_dual_control:
        needed.append(("pay and return decisions", "exception:decide"))
    if tenant.sensitive_changes_need_approval:
        needed += sorted({(k.title.lower(), k.permission) for k in change_kinds.all_kinds() if k.tier == "sensitive" and k.scope == "bank"})
    if tenant.access_changes_need_approval:
        needed.append(("people's access", "user:manage"))
    if tenant.customer_changes_need_approval:
        needed.append(("customers' changes", "customer:manage"))
    if tenant.attest_second_person:
        needed.append(("CUEC attestations", "compliance:attest"))
    short = [f"{what} (needs {permission})" for what, permission in needed if not enough(permission)]
    evidence = {"rules": [what for what, _p in needed], "without_a_second_approver": short,
                "sensitive_settings_protected": bool(tenant.sensitive_changes_need_approval)}
    if short:
        return Control("second_approver", title, WARN, f"Only one person could approve {'; '.join(short)}: changes there take effect after "
                                                       "24 hours, and decisions wait to their deadline. Give a second person the permission.", evidence)
    return Control("second_approver", title, PASS, f"Each of your {len(needed)} second-person rules has someone else to approve.", evidence)


def sensitive_settings_protected(session: Session, tenant_id: uuid.UUID) -> Control:
    """Sensitive settings wait for a second admin at a bank that has one (DUAL_CONTROL_PLAN.md DC5):
    the switch is off for new banks, which start with one admin (DC-D16), so this says when it can
    be turned on. Separate from second_approver, so that one's turning to WARN means an approver went."""
    from openclear.domain.tenant import Tenant
    from openclear.services import change_request_service

    tenant = session.get(Tenant, tenant_id)
    title = "Sensitive settings need a second admin"
    admins = len(change_request_service.approvers(session, tenant_id, None, "tenant:manage", excluding=None))
    evidence = {"sensitive_settings_protected": bool(tenant.sensitive_changes_need_approval), "admins": admins}
    if tenant.sensitive_changes_need_approval:
        return Control("sensitive_settings_protected", title, PASS, "Rules, models, security groups, sign-on, API clients and webhooks wait "
                                                                    "for a second admin.", evidence)
    if admins >= 2:
        return Control("sensitive_settings_protected", title, WARN, "Sensitive settings (rules, models, security groups, sign-on, API clients, "
                                                                    "webhooks) don't need a second admin, though you have one. Turn it on "
                                                                    "under Settings → Dual control.", evidence)
    return Control("sensitive_settings_protected", title, PASS, "Off, as there's only one admin to approve; it can be turned on once there's "
                                                                "a second.", evidence)
