# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Continuous control monitoring (COMPLIANCE_PLAN.md C4): each control's live status,
`PASS`, `WARN` or `FAIL`, with the evidence for it, read from what the bank's installation
is actually doing. Shown at Admin → Controls and `GET /api/v1/controls/status`, and
included in the evidence export (C3).

A control reports only what OpenClear can see. Disk and database encryption are the host's
job: the operator states how they're done (storage_encryption_attestation), and the control
shows that statement rather than claiming to have checked it."""

import time
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
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
    from openclear.services import audit_archive_service, audit_log_service

    chain = audit_log_service.verify_chain(session, tenant_id)
    archives = audit_archive_service.verify_archives(session, tenant_id)
    evidence = {"live_entries_checked": chain.checked_count, "archives_checked": archives.checked_archives,
                "archived_entries_checked": archives.checked_entries}
    if not chain.valid:
        return Control("audit_chain", "Action log intact", FAIL, f"The action log doesn't verify at entry {chain.broken_at_entry_id}.",
                       {**evidence, "broken_at_entry_id": str(chain.broken_at_entry_id)})
    if not archives.valid:
        return Control("audit_chain", "Action log intact", FAIL, archives.problem or "An archive doesn't verify.", evidence)
    return Control("audit_chain", "Action log intact", PASS,
                   f"Every signature and link verifies ({chain.checked_count} live entries, plus archives).", evidence)


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


def _api_client_secrets(session: Session, tenant_id: uuid.UUID) -> Control:
    from openclear.domain.api_client import ApiClient

    cutoff = _now() - timedelta(days=SECRET_MAX_AGE_DAYS)
    clients = session.execute(select(ApiClient).where(ApiClient.tenant_id == tenant_id, ApiClient.revoked_at.is_(None))).scalars().all()
    old = [c.name for c in clients if _aware(c.secret_rotated_at or c.created_at) and _aware(c.secret_rotated_at or c.created_at) < cutoff]
    evidence = {"active_clients": len(clients), "older_than_a_year": old}
    if old:
        return Control("api_client_secrets", "Integration secrets rotated", WARN,
                       f"{len(old)} API clients haven't had their secret rotated in over a year: {', '.join(old[:10])}.", evidence)
    return Control("api_client_secrets", "Integration secrets rotated", PASS,
                   f"All {len(clients)} active API clients rotated their secret within a year.", evidence)


def _anomaly_capture(session: Session, tenant_id: uuid.UUID) -> Control:
    from openclear.services import mrm_service

    run = mrm_service.latest(session, tenant_id, mrm_service.ANOMALY_CAPTURE)
    if run is None:
        return Control("anomaly_capture", "Synthetic fraud caught", WARN,
                       "The anomaly-capture test hasn't run. Run it at Admin → Model risk, or turn on the daily schedule.", {})
    evidence = {"run_id": str(run.id), "started_at": run.started_at.isoformat() if run.started_at else None,
                "caught": run.report.get("caught"), "total": run.report.get("total")}
    if not run.passed:
        return Control("anomaly_capture", "Synthetic fraud caught", FAIL, run.summary, evidence)
    if _aware(run.started_at) < _now() - CAPTURE_MAX_AGE:
        return Control("anomaly_capture", "Synthetic fraud caught", WARN, "The last run passed, but it's more than a day old.", evidence)
    return Control("anomaly_capture", "Synthetic fraud caught", PASS, run.summary, evidence)


def status(session: Session, tenant_id: uuid.UUID) -> list[Control]:
    return [
        _field_encryption(session, tenant_id),
        _storage_encryption(),
        _audit_chain(session, tenant_id),
        _sod(session, tenant_id),
        _admin_mfa(session, tenant_id),
        _anomaly_capture(session, tenant_id),
        _parser_hardening(),
        _sla(session, tenant_id),
        _api_client_secrets(session, tenant_id),
        _production_safety(),
    ]


def summary(controls: list[Control]) -> dict:
    overall = max((c.status for c in controls), key=_ORDER.__getitem__, default=PASS)
    return {"overall": overall, "checked_at": _now().isoformat(), "counts": {s: sum(1 for c in controls if c.status == s) for s in _ORDER},
            "controls": [c.as_dict() for c in controls]}
