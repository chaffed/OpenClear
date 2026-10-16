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
    last = _aware(user.last_login_at) or _aware(membership.created_at)
    return last is not None and last < now - timedelta(days=DORMANT_DAYS)


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
    evidence = {"active_staff": len(staff), "no_sign_in_for_90_days": dormant[:LIST_LIMIT], "dormant": len(dormant)}
    title = "Dormant staff accounts"
    if dormant:
        return Control("dormant_accounts", title, WARN,
                       f"{_plural(len(dormant), 'staff member')} haven't signed in for {DORMANT_DAYS} days: {', '.join(dormant[:10])}. "
                       "Deactivate anyone who's left.", evidence)
    return Control("dormant_accounts", title, PASS, f"All {len(staff)} active staff signed in within {DORMANT_DAYS} days.", evidence)


def customer_dormant_users(session: Session, tenant_id: uuid.UUID) -> Control:
    now = _now()
    people = _people(session, tenant_id, customers=True)
    dormant = sorted(u.email for u, m, _p in people if _dormant(u, m, now))
    evidence = {"active_customer_people": len(people), "no_sign_in_for_90_days": dormant[:LIST_LIMIT], "dormant": len(dormant)}
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

    title = "Rule and model changes approved by a second admin"
    approved = session.execute(select(ChangeRequest).where(ChangeRequest.tenant_id == tenant_id, ChangeRequest.status == "approved",
                                                           ChangeRequest.decided_at >= start, ChangeRequest.decided_at < end)).scalars().all()
    same = [c for c in approved if c.decided_by_user_id == c.requested_by_user_id]
    evidence = {"approved_changes": len(approved), "approved_by_their_requester": len(same), "period": [start.isoformat(), end.isoformat()]}
    if same:
        return Control("two_person_changes", title, FAIL, f"{len(same)} of {len(approved)} changes were approved by the person who asked.", evidence)
    return Control("two_person_changes", title, PASS, f"All {len(approved)} changes approved in the period had a second admin.", evidence)


def change_impact_replay(session: Session, tenant_id: uuid.UUID, start: datetime, end: datetime) -> Control:
    from openclear.domain.change_request import ChangeRequest
    from openclear.domain.control_run import ControlRun
    from openclear.services.mrm_service import REPLAY

    title = "Changes replayed before approval"
    approved = session.execute(select(ChangeRequest).where(ChangeRequest.tenant_id == tenant_id, ChangeRequest.status == "approved",
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
