# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Continuous control monitoring (COMPLIANCE_PLAN.md C4): each control's live status,
`PASS`, `WARN` or `FAIL`, with the evidence for it, read from what the bank's installation
is actually doing. Shown at Settings → Controls and `GET /api/v1/controls/status`, and
included in the evidence export (C3).

A control reports only what OpenClear can see. Disk and database encryption are the host's
job: the operator states how they're done (storage_encryption_attestation), and the control
shows that statement rather than claiming to have checked it."""

import time
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from openclear.config import get_settings

PASS, WARN, FAIL = "PASS", "WARN", "FAIL"
_ORDER = {PASS: 0, WARN: 1, FAIL: 2}
ADMIN_PERMISSIONS = {"admin:manage", "user:manage", "security_group:manage", "tenant:manage", "api_client:manage"}
SECRET_MAX_AGE_DAYS = 365
CAPTURE_MAX_AGE = timedelta(hours=26)
PARSER_SECONDS = 2.0


@dataclass
class Control:
    key: str
    title: str
    status: str
    detail: str
    evidence: dict

    def as_dict(self) -> dict:
        return asdict(self)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime | None) -> datetime | None:
    return value.replace(tzinfo=timezone.utc) if value is not None and value.tzinfo is None else value


def _field_encryption(session: Session, tenant_id: uuid.UUID) -> Control:
    from openclear.auth import crypto

    counts = crypto.inventory(session, tenant_id)
    if counts["outdated"]:
        return Control("field_encryption", "Secrets encrypted in the database", WARN,
                       f"{counts['outdated']} stored secrets aren't yet AES-256-GCM under the current key. They're re-encrypted when the "
                       "application next starts.", {**counts, "scheme": "AES-256-GCM", "key_id": crypto.current_key_id()})
    return Control("field_encryption", "Secrets encrypted in the database", PASS,
                   f"All {counts['current']} stored secrets are AES-256-GCM under the current key.",
                   {**counts, "scheme": "AES-256-GCM", "key_id": crypto.current_key_id()})


def _storage_encryption() -> Control:
    statement = get_settings().storage_encryption_attestation
    if statement:
        return Control("storage_encryption", "Disk and database encryption", PASS, f"Stated by the operator: {statement}",
                       {"attestation": statement, "verified_by_openclear": False})
    return Control("storage_encryption", "Disk and database encryption", WARN,
                   "Disk and database encryption are the host's job, and OpenClear can't see them. Verify them with your provider, "
                   "then record how they're done in OPENCLEAR_STORAGE_ENCRYPTION_ATTESTATION.", {"attestation": None})


def _sla(session: Session, tenant_id: uuid.UUID) -> Control:
    from openclear.domain.bulk_upload_file import BulkUploadFile, BulkUploadStatus
    from openclear.domain.decision_outbox import DecisionOutbox
    from openclear.domain.exception_item import ExceptionItem, ExceptionStatus

    now = _now()
    open_statuses = [ExceptionStatus.OPEN, ExceptionStatus.PENDING_APPROVAL]
    base = select(func.count()).select_from(ExceptionItem).where(ExceptionItem.tenant_id == tenant_id, ExceptionItem.status.in_(open_statuses),
                                                                  ExceptionItem.decision_deadline.is_not(None))
    overdue = session.execute(base.where(ExceptionItem.decision_deadline <= now)).scalar_one()
    near = session.execute(base.where(ExceptionItem.decision_deadline > now, ExceptionItem.decision_deadline <= now + timedelta(hours=2))).scalar_one()
    stuck = session.execute(select(func.count()).select_from(BulkUploadFile).where(
        BulkUploadFile.tenant_id == tenant_id,
        BulkUploadFile.status.in_([BulkUploadStatus.RECEIVED, BulkUploadStatus.QUEUED, BulkUploadStatus.PROCESSING]),
        BulkUploadFile.uploaded_at <= now - timedelta(hours=1))).scalar_one()
    outbox = session.execute(select(DecisionOutbox).where(DecisionOutbox.tenant_id == tenant_id)).scalar_one_or_none()
    outbox_late = False
    if outbox is not None and outbox.enabled:
        last = _aware(outbox.last_run_at)
        outbox_late = last is None or last < now - timedelta(minutes=outbox.interval_minutes * 2 + 15)
    evidence = {"overdue_exceptions": overdue, "due_within_2_hours": near, "imports_stuck_over_an_hour": stuck,
                "decision_outbox_late": outbox_late}
    if overdue:
        return Control("sla", "Decisions and imports on time", FAIL, f"{overdue} exceptions are past their decision deadline.", evidence)
    problems = [text for count, text in ((near, f"{near} exceptions are due within 2 hours"),
                                         (stuck, f"{stuck} imports have been waiting over an hour"),
                                         (outbox_late, "the decision outbox hasn't run on schedule")) if count]
    if problems:
        return Control("sla", "Decisions and imports on time", WARN, "; ".join(problems).capitalize() + ".", evidence)
    return Control("sla", "Decisions and imports on time", PASS, "Nothing overdue, nothing stuck.", evidence)


def _parser_hardening() -> Control:
    """The malformed-file corpus through the real parsers: each must be refused cleanly
    (a parse error, not a crash or a half-read file) and quickly."""
    from openclear.services.mrm_service import _malformed_file_probes

    started = time.monotonic()
    probes = _malformed_file_probes()
    elapsed = time.monotonic() - started
    failed = [p for p in probes if not p.caught]
    evidence = {"files": len(probes), "seconds": round(elapsed, 3),
                "results": [{"file": p.key, "result": p.got} for p in probes]}
    if failed:
        return Control("parser_hardening", "Malformed files refused", FAIL,
                       "Not refused cleanly: " + ", ".join(p.key for p in failed) + ".", evidence)
    if elapsed > PARSER_SECONDS:
        return Control("parser_hardening", "Malformed files refused", WARN, f"All refused, but it took {elapsed:.1f}s.", evidence)
    return Control("parser_hardening", "Malformed files refused", PASS, f"All {len(probes)} malformed X9.37 and NACHA files refused cleanly.",
                   evidence)


def _audit_chain(session: Session, tenant_id: uuid.UUID) -> Control:
    """From the latest verification run (services/long_runs.py), which runs daily and on
    demand, rather than walking the whole log on every view. The first time, it's verified
    now and kept."""
    from openclear.services import long_runs

    run = long_runs.latest_verification(session, tenant_id) or long_runs.verify_now(session, tenant_id)
    report = run.report or {}
    evidence = {"live_entries_checked": report.get("checked_count"), "archives_checked": report.get("archives_checked"),
                "archived_entries_checked": report.get("archived_entries_checked"), "verified_at": _iso(run.started_at),
                "run_id": str(run.id)}
    if not run.passed:
        return Control("audit_chain", "Action log intact", FAIL, run.summary,
                       {**evidence, "broken_at_entry_id": report.get("broken_at_entry_id")})
    if _aware(run.started_at) < _now() - long_runs.VERIFICATION_MAX_AGE:
        return Control("audit_chain", "Action log intact", WARN,
                       f"The last verification passed, but it's more than a day old ({run.summary}). Verify it again from the "
                       "Action Log, or turn on the daily schedule.", evidence)
    return Control("audit_chain", "Action log intact", PASS, f"{run.summary}.", evidence)


def _iso(value):
    return _aware(value).isoformat() if value else None


def _sod(session: Session, tenant_id: uuid.UUID) -> Control:
    from openclear.auth import sod
    from openclear.domain.audit_log_entry import AuditLogEntry
    from openclear.services import security_group_service

    mode = sod.mode(session, tenant_id)
    conflicted = [g.name for g in security_group_service.list_security_groups(session, tenant_id) if sod.group_conflicts(g.permissions)]
    since = _now() - timedelta(days=30)
    violations = session.execute(select(func.count()).select_from(AuditLogEntry).where(
        AuditLogEntry.tenant_id == tenant_id, AuditLogEntry.action == "sod.violation", AuditLogEntry.occurred_at >= since)).scalar_one()
    evidence = {"mode": mode, "groups_combining_duties": conflicted, "violations_last_30_days": violations}
    if mode == "enforce" and not conflicted:
        return Control("segregation_of_duties", "Segregation of duties", PASS,
                       f"Enforced; no group combines conflicting duties. {violations} refused attempts in the last 30 days.", evidence)
    detail = "Warn only: breaches are allowed and logged" + (f"; {', '.join(conflicted)} combine conflicting duties" if conflicted else "")
    return Control("segregation_of_duties", "Segregation of duties", WARN, f"{detail}. {violations} breaches in the last 30 days.", evidence)


def _admin_mfa(session: Session, tenant_id: uuid.UUID) -> Control:
    """People who can change who may do what: each should have a second factor, a security
    key or passkey here, or sign in through the bank's identity provider only."""
    from openclear.domain.security_group import SecurityGroup
    from openclear.domain.tenant import Tenant
    from openclear.domain.tenant_membership import TenantMembership
    from openclear.domain.user import User
    from openclear.domain.webauthn_credential import WebauthnCredential

    tenant = session.get(Tenant, tenant_id)
    rows = session.execute(select(User.id, User.email, SecurityGroup.permissions).join(TenantMembership, TenantMembership.user_id == User.id)
                           .join(SecurityGroup, SecurityGroup.id == TenantMembership.security_group_id)
                           .where(TenantMembership.tenant_id == tenant_id, TenantMembership.is_active.is_(True), User.is_active.is_(True))).all()
    admins = {user_id: email for user_id, email, perms in rows if ADMIN_PERMISSIONS & set(perms or [])}
    if not tenant.password_login_enabled:
        return Control("admin_mfa", "Admins use a second factor", PASS,
                       f"Password sign-in is off: all {len(admins)} admins sign in through your identity provider.",
                       {"admins": len(admins), "sso_only": True})
    with_keys = set(session.execute(select(WebauthnCredential.user_id).where(WebauthnCredential.tenant_id == tenant_id,
                                                                             WebauthnCredential.user_id.in_(list(admins)))).scalars())
    without = sorted(email for user_id, email in admins.items() if user_id not in with_keys)
    evidence = {"admins": len(admins), "with_security_key": len(with_keys), "without": without[:50]}
    if without:
        return Control("admin_mfa", "Admins use a second factor", WARN,
                       f"{len(without)} of {len(admins)} admins have no security key or passkey.", evidence)
    return Control("admin_mfa", "Admins use a second factor", PASS, f"All {len(admins)} admins have a security key or passkey.", evidence)


def _production_safety() -> Control:
    from openclear.config import assert_production_safe

    settings = get_settings()
    if settings.environment != "production":
        return Control("production_safety", "Production settings", WARN,
                       f"Running as {settings.environment!r}, so the production safety checks aren't applied.",
                       {"environment": settings.environment})
    try:
        assert_production_safe(settings)
    except RuntimeError as exc:
        return Control("production_safety", "Production settings", FAIL, str(exc), {"environment": "production"})
    return Control("production_safety", "Production settings", PASS, "Real keys, and no development defaults or unsafe options.",
                   {"environment": "production"})


def _edge() -> Control:
    """Whether an edge network (Cloudflare, Akamai, a load balancer) is in front, and whether
    OpenClear is set up for it (WAF_PLAN.md W1–W2). Read from the request being served."""
    from openclear.web import request_context

    settings = get_settings()
    context = request_context.current()
    hints = list(context.edge_hints) if context else []
    configured = bool(settings.client_ip_header) or settings.trusted_proxy_count > 0
    lockdown = bool(settings.edge_secret) or bool(settings.edge_attestation)
    evidence = {
        "edge_headers_seen": hints, "client_ip_header": settings.client_ip_header, "trusted_proxy_count": settings.trusted_proxy_count,
        "trusted_proxy_ranges": len(request_context.trusted_networks()), "edge_secret": bool(settings.edge_secret),
        "edge_attestation": settings.edge_attestation,
        "this_request": {"client_ip_from": context.ip_source, "edge_trusted": context.edge_trusted} if context else None,
    }
    title = "Edge network set up"
    if hints and not configured:
        return Control("edge", title, FAIL,
                       f"An edge network is in front ({', '.join(hints)} headers arrive), but OpenClear isn't set up to read the "
                       "caller's address from it, so every visitor looks like the edge: rate limits, the action log and security "
                       "events see the edge's address. Set OPENCLEAR_CLIENT_IP_HEADER and OPENCLEAR_TRUSTED_PROXY_CIDRS.", evidence)
    if settings.client_ip_header and hints and context and not context.edge_trusted:
        return Control("edge", title, WARN,
                       f"Edge headers arrived from {context.peer_ip}, which isn't in OPENCLEAR_TRUSTED_PROXY_CIDRS. Either the ranges "
                       "are out of date, or something is reaching OpenClear around the edge.", evidence)
    if not configured:
        return Control("edge", title, PASS, "No edge network detected; the caller's address comes from the connection.", evidence)
    if not lockdown:
        return Control("edge", title, WARN,
                       "OpenClear reads the caller's address from the edge, but nothing shows the origin only accepts traffic from it. "
                       "Set OPENCLEAR_EDGE_SECRET (the edge adds it to each request), or record the tunnel, mTLS or firewall that "
                       "does this in OPENCLEAR_EDGE_ATTESTATION.", evidence)
    how = "a shared secret from the edge" if settings.edge_secret else f"stated by the operator: {settings.edge_attestation}"
    return Control("edge", title, PASS, f"The caller's address comes from the edge, and the origin is locked down ({how}).", evidence)


def _api_client_secrets(session: Session, tenant_id: uuid.UUID) -> Control:
    from openclear.domain.api_client import ApiClient

    cutoff = _now() - timedelta(days=SECRET_MAX_AGE_DAYS)
    clients = session.execute(select(ApiClient).where(ApiClient.tenant_id == tenant_id, ApiClient.revoked_at.is_(None))).scalars().all()
    # A client signing in with a key pair has no shared secret to rotate: its private key
    # never leaves the bank's own systems.
    with_secrets = [c for c in clients if c.token_auth_method != "private_key_jwt"]
    old = [c.name for c in with_secrets if _aware(c.secret_rotated_at or c.created_at) and _aware(c.secret_rotated_at or c.created_at) < cutoff]
    evidence = {"active_clients": len(clients), "signing_in_with_a_key_pair": len(clients) - len(with_secrets), "older_than_a_year": old}
    if old:
        return Control("api_client_secrets", "Integration secrets rotated", WARN,
                       f"{len(old)} API clients haven't had their secret rotated in over a year: {', '.join(old[:10])}.", evidence)
    return Control("api_client_secrets", "Integration secrets rotated", PASS,
                   f"All {len(clients)} active API clients rotated their secret within a year.", evidence)


def _customer_self_service(session: Session, tenant_id: uuid.UUID) -> Control:
    """Which customers manage their own people and sign-on (CUSTOMER_ADMIN_PLAN.md), with
    what, and whether each that manages its people has someone to do it."""
    from openclear.domain.customer import Customer
    from openclear.domain.customer_admin import CustomerEmailDomain
    from openclear.domain.security_group import SecurityGroup
    from openclear.domain.sso_connection import SsoConnection
    from openclear.services import customer_people_service

    title = "Customer self-service"
    customers = session.execute(select(Customer).where(Customer.tenant_id == tenant_id, Customer.is_active.is_(True), or_(
        Customer.self_manages_users.is_(True), Customer.self_manages_sso.is_(True)))).scalars().all()
    if not customers:
        return Control("customer_self_service", title, PASS, "No customer manages its own people or sign-on; the bank does.", {})
    evidence = {
        "manage_their_people": sorted(c.name for c in customers if c.self_manages_users),
        "manage_their_sign_on": sorted(c.name for c in customers if c.self_manages_sso),
        "roles_offered": sorted(session.execute(select(SecurityGroup.name).where(
            SecurityGroup.tenant_id == tenant_id, SecurityGroup.offered_to_customers.is_(True))).scalars()),
        "customer_managed_connections": session.execute(select(func.count()).select_from(SsoConnection).where(
            SsoConnection.tenant_id == tenant_id, SsoConnection.managed_by == "customer", SsoConnection.is_active.is_(True))).scalar_one(),
        "verified_email_domains": sorted(session.execute(select(CustomerEmailDomain.domain).where(
            CustomerEmailDomain.tenant_id == tenant_id, CustomerEmailDomain.verified_at.is_not(None))).scalars()),
    }
    unattended = sorted(c.name for c in customers if c.self_manages_users
                        and not customer_people_service.administrators(session, tenant_id, c.id))
    evidence["without_an_administrator"] = unattended
    if unattended:
        return Control("customer_self_service", title, WARN,
                       f"{len(unattended)} customers manage their own people but have no administrator to do it: {', '.join(unattended[:10])}.",
                       evidence)
    return Control("customer_self_service", title, PASS,
                   f"{len(customers)} customers manage their own people or sign-on, within the roles you offer.", evidence)


def _anomaly_capture(session: Session, tenant_id: uuid.UUID) -> Control:
    from openclear.services import mrm_service

    run = mrm_service.latest(session, tenant_id, mrm_service.ANOMALY_CAPTURE)
    if run is None:
        return Control("anomaly_capture", "Synthetic fraud caught", WARN,
                       "The anomaly-capture test hasn't run. Run it at Settings → Model risk, or turn on the daily schedule.", {})
    evidence = {"run_id": str(run.id), "started_at": run.started_at.isoformat() if run.started_at else None,
                "caught": run.report.get("caught"), "total": run.report.get("total")}
    if not run.passed:
        return Control("anomaly_capture", "Synthetic fraud caught", FAIL, run.summary, evidence)
    if _aware(run.started_at) < _now() - CAPTURE_MAX_AGE:
        return Control("anomaly_capture", "Synthetic fraud caught", WARN, "The last run passed, but it's more than a day old.", evidence)
    return Control("anomaly_capture", "Synthetic fraud caught", PASS, run.summary, evidence)


def _event_delivery(session: Session, tenant_id: uuid.UUID) -> Control:
    """Security events reach the bank's SIEM (SIEM_PLAN.md S5): through a destination that
    isn't failing or falling behind, or, with none, by the bank reading the pull API."""
    from openclear.domain.tenant import Tenant
    from openclear.services.siem import delivery

    title = "Security events delivered"
    statuses = delivery.status_for_bank(session, tenant_id)
    evidence = {"destinations": [{
        "destination": delivery.describe(s.destination), "receives": "bank" if s.destination.tenant_id else "installation",
        "last_success_at": _iso(s.destination.last_success_at), "failing_since": _iso(s.destination.failing_since),
        "last_error": s.destination.last_error, "waiting": s.backlog, "oldest_waiting_at": _iso(s.oldest_pending_at),
    } for s in statuses]}
    if not statuses:
        tenant = session.get(Tenant, tenant_id)
        read_at = _aware(tenant.security_event_feed_read_at) if tenant else None
        evidence["feed_last_read_at"] = _iso(read_at)
        if read_at is None or read_at < _now() - timedelta(days=1):
            return Control("event_delivery", title, WARN,
                           "No SIEM destination is set, and nobody has read the security events through the API in the last day. "
                           "Add one in Settings → Security event forwarding.", evidence)
        return Control("event_delivery", title, PASS, "No destination is set; the events are read through the API.", evidence)
    stale = [s for s in statuses if s.stale]
    if stale:
        problems = [f"{delivery.describe(s.destination)}: " + (f"failing ({s.destination.last_error})" if s.destination.failing_since
                                                              else f"{s.backlog} waiting") for s in stale]
        return Control("event_delivery", title, FAIL, "Not delivered for over an hour: " + "; ".join(problems) + ".", evidence)
    parts = []
    for s in statuses:
        last = _aware(s.destination.last_success_at)
        parts.append(f"{delivery.describe(s.destination)}, " + (f"last delivered {last:%Y-%m-%d %H:%M} UTC" if last else "nothing to send yet")
                     + (f", {s.backlog} waiting" if s.backlog else ""))
    return Control("event_delivery", title, PASS, "; ".join(parts) + ".", evidence)


def status(session: Session, tenant_id: uuid.UUID) -> list[Control]:
    controls = _status(session, tenant_id)
    record_changes(tenant_id, controls)
    return controls


def record_changes(tenant_id: uuid.UUID, controls: list[Control]) -> list[tuple[str, str | None, str]]:
    """Keeps each control's last status, and records a `control.status.changed` security event
    when one changes (to WARN or FAIL, or back to PASS), so a bank's SIEM can alert on it
    (SIEM_PLAN.md). The first status seen for a control is kept without an event. In its own
    transaction: the controls are mostly read on requests that change nothing. Never raises.
    Returns the changes (key, old, new)."""
    import logging

    from sqlalchemy.exc import IntegrityError

    from openclear.db.tenancy import bind_session_to_tenant
    from openclear.domain.control_status import ControlStatus
    from openclear.services import security_events

    changes: list[tuple[str, str | None, str]] = []
    try:
        from openclear.db.session import get_session_factory

        session = (security_events._detached_factory or get_session_factory())()
        try:
            bind_session_to_tenant(session, tenant_id)
            known = {row.key: row for row in session.execute(select(ControlStatus).where(ControlStatus.tenant_id == tenant_id)).scalars()}
            for control in controls:
                row = known.get(control.key)
                if row is None:
                    session.add(ControlStatus(tenant_id=tenant_id, key=control.key, status=control.status))
                    continue
                if row.status == control.status:
                    continue
                changes.append((control.key, row.status, control.status))
                severity_reason = f"{row.status}_to_{control.status}".lower()
                security_events.emit(session, "control.status.changed", tenant_id=tenant_id, outcome="failure" if control.status == FAIL else "success",
                                     reason=severity_reason, actor_type="system", target_type="control", target_id=control.key,
                                     target_label=control.title, details={"from": row.status, "to": control.status, "detail": control.detail[:300]},
                                     channel="system")
                row.status, row.changed_at = control.status, _now()
            session.commit()
        except IntegrityError:  # another request recorded the same first status at the same moment
            session.rollback()
        finally:
            session.close()
    except Exception:  # noqa: BLE001 -- reading the controls must never fail because a change couldn't be recorded
        logging.getLogger(__name__).exception("Couldn't record control status changes")
    return changes


def _status(session: Session, tenant_id: uuid.UUID) -> list[Control]:
    return [
        _field_encryption(session, tenant_id),
        _storage_encryption(),
        _edge(),
        _audit_chain(session, tenant_id),
        _sod(session, tenant_id),
        _admin_mfa(session, tenant_id),
        _anomaly_capture(session, tenant_id),
        _parser_hardening(),
        _sla(session, tenant_id),
        _api_client_secrets(session, tenant_id),
        _customer_self_service(session, tenant_id),
        _event_delivery(session, tenant_id),
        _production_safety(),
    ]


def summary(controls: list[Control]) -> dict:
    overall = max((c.status for c in controls), key=_ORDER.__getitem__, default=PASS)
    return {"overall": overall, "checked_at": _now().isoformat(), "counts": {s: sum(1 for c in controls if c.status == s) for s in _ORDER},
            "controls": [c.as_dict() for c in controls]}
