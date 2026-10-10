# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""The evidence export (COMPLIANCE_PLAN.md C3; 2023 Interagency Guidance on Third-Party
Relationships): one signed bundle an examiner or auditor can take away for a period.

It holds what the installation is running (its version and database revision, the rules,
plugins and models in force with digests of each), the bank's settings and security
groups, whether the action log verifies, segregation-of-duties breaches, changes and their
approvals, every decision in the period with who recommended and who decided it, the
model-risk test runs, and a live snapshot of the controls (services/controls_service.py).

The bundle is canonical JSON, signed with the installation's file-signing key (ECDSA P-256,
SHA-256). The JSON export carries the signature and the public key to check it with; the PDF
carries the JSON's digest and signature on its last page, and the PDF's own signature in
response headers. verify() checks a JSON export."""

import hashlib
import inspect
import json
import uuid
from datetime import date, datetime, time, timedelta, timezone
from importlib.metadata import PackageNotFoundError, version

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

MAX_DECISIONS = 50_000
MAX_DAYS = 400


class EvidenceError(ValueError):
    """The request can't be met; the message is fit to show."""


def _iso(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime) and value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def canonical(bundle: dict) -> bytes:
    return json.dumps(bundle, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str).encode("utf-8")


def _period(start: date, end: date) -> tuple[datetime, datetime]:
    if end < start:
        raise EvidenceError("end is before start.")
    if (end - start).days > MAX_DAYS:
        raise EvidenceError(f"At most {MAX_DAYS} days per export; export a longer period in parts.")
    return datetime.combine(start, time.min, timezone.utc), datetime.combine(end + timedelta(days=1), time.min, timezone.utc)


def _system(session: Session) -> dict:
    from sqlalchemy import text

    try:
        installed = version("openclear")
    except PackageNotFoundError:
        installed = "unknown"
    try:
        with session.begin_nested():
            revision = session.execute(text("SELECT version_num FROM alembic_version")).scalar()
    except Exception:  # noqa: BLE001 -- a database made without Alembic (the test suite)
        revision = None
    return {"openclear_version": installed, "database_revision": revision}


def _module_digest(obj) -> dict:
    module = inspect.getmodule(obj)
    try:
        source = inspect.getsource(module)
    except (OSError, TypeError):
        return {"module": getattr(module, "__name__", None), "sha256": None}
    return {"module": module.__name__, "sha256": _sha256_text(source)}


def _rules(session: Session, tenant_id: uuid.UUID) -> dict:
    from openclear import plugins
    from openclear.networks.ach import adapter as ach_adapter, rules as ach_rules
    from openclear.networks.check import adapter as check_adapter, rules as check_rules
    from openclear.services import plugin_service

    built_in = [{"network": "check", **_module_digest(check_rules)}, {"network": "check", **_module_digest(check_adapter)},
                {"network": "ach", **_module_digest(ach_rules)}, {"network": "ach", **_module_digest(ach_adapter)}]
    statuses = {p.name: p for p in plugins.status()}
    plugin_rules = []
    for name, rule in sorted(plugins.registry.matching_rules.items()):
        plugin_rules.append({"name": name, "network": rule.network, "enabled": plugin_service.is_enabled(session, tenant_id, "matching_rules", name),
                             **_module_digest(type(rule) if not inspect.ismodule(rule) else rule)})
    loaded = [{"name": p.name, "version": p.version, "source": p.source, "status": p.status} for p in statuses.values()]
    return {"built_in": built_in, "plugin_matching_rules": plugin_rules, "plugins": loaded}


def _models(session: Session, tenant_id: uuid.UUID) -> list[dict]:
    from openclear.domain.customer import Customer
    from openclear.domain.ml_model import MlModel, MlModelStatus

    customers = select(Customer.id).where(Customer.tenant_id == tenant_id)
    rows = session.execute(select(MlModel).where(MlModel.status == MlModelStatus.ACTIVE, or_(
        MlModel.tenant_id == tenant_id, MlModel.customer_id.in_(customers),
        (MlModel.tenant_id.is_(None) & MlModel.customer_id.is_(None))))).scalars()
    return [{"id": str(m.id), "network": m.network_code, "scope": "customer" if m.customer_id else ("bank" if m.tenant_id else "shared"),
             "customer_id": str(m.customer_id) if m.customer_id else None, "version": m.version, "algorithm": m.algorithm,
             "artifact_sha256": m.artifact_sha256, "trained_from_decisions": m.trained_from_decision_count,
             "metrics": m.metrics_json, "activated_at": _iso(m.activated_at)} for m in rows]


def _settings(session: Session, tenant_id: uuid.UUID) -> dict:
    from openclear.auth import sod
    from openclear.domain.tenant import Tenant
    from openclear.services import security_group_service

    tenant = session.get(Tenant, tenant_id)
    groups = [{"name": g.name, "permissions": sorted(g.permissions or []), "conflicts": [r.key for r in sod.group_conflicts(g.permissions)]}
              for g in security_group_service.list_security_groups(session, tenant_id)]
    return {"require_dual_control": tenant.require_dual_control, "segregation_of_duties": tenant.sod_mode,
            "stale_date_threshold_days": tenant.stale_date_threshold_days, "password_login_enabled": tenant.password_login_enabled,
            "security_groups": groups}


def _audit(session: Session, tenant_id: uuid.UUID, start: datetime, end: datetime) -> dict:
    from openclear.domain.audit_log_entry import AuditLogEntry
    from openclear.services import audit_archive_service, audit_log_service

    chain = audit_log_service.verify_chain(session, tenant_id)
    archives = audit_archive_service.verify_archives(session, tenant_id)

    def entries(*actions):
        rows = session.execute(select(AuditLogEntry).where(
            AuditLogEntry.tenant_id == tenant_id, AuditLogEntry.action.in_(actions),
            AuditLogEntry.occurred_at >= start, AuditLogEntry.occurred_at < end).order_by(AuditLogEntry.sequence)).scalars()
        return [{"sequence": e.sequence, "occurred_at": _iso(e.occurred_at), "actor_user_id": str(e.actor_user_id) if e.actor_user_id else None,
                 "channel": e.channel.value, "action": e.action, "summary": e.summary, "metadata": e.metadata_json,
                 "entry_hash": e.entry_hash} for e in rows]

    period_count = session.execute(select(AuditLogEntry.sequence).where(
        AuditLogEntry.tenant_id == tenant_id, AuditLogEntry.occurred_at >= start, AuditLogEntry.occurred_at < end)).all()
    return {
        "chain_verifies": chain.valid, "live_entries_checked": chain.checked_count,
        "broken_at_entry_id": str(chain.broken_at_entry_id) if chain.broken_at_entry_id else None,
        "archives_verify": archives.valid, "archives_checked": archives.checked_archives, "archive_problem": archives.problem,
        "entries_in_period": len(period_count),
        "sod_violations": entries("sod.violation"),
        "changes": entries("change_request.create", "change_request.approve", "change_request.reject", "tenant.update_sod_mode",
                           "plugin.enable", "plugin.disable", "security_group.create", "security_group.update", "security_group.delete"),
    }


def _decisions(session: Session, tenant_id: uuid.UUID, start: datetime, end: datetime) -> dict:
    from openclear.domain.decision import Decision
    from openclear.domain.exception_item import ExceptionItem
    from openclear.domain.user import User

    rows = session.execute(select(Decision, ExceptionItem).join(ExceptionItem, ExceptionItem.id == Decision.exception_item_id).where(
        Decision.tenant_id == tenant_id, Decision.decided_at >= start, Decision.decided_at < end).order_by(Decision.decided_at)
        .limit(MAX_DECISIONS + 1)).all()
    truncated = len(rows) > MAX_DECISIONS
    rows = rows[:MAX_DECISIONS]
    user_ids = {d.decided_by_user_id for d, _e in rows} | {d.submitted_by_user_id for d, _e in rows} | {e.recommended_by_user_id for _d, e in rows}
    emails = dict(session.execute(select(User.id, User.email).where(User.id.in_([u for u in user_ids if u]))).all()) if user_ids else {}

    def who(user_id):
        return emails.get(user_id) if user_id else None

    items = [{"decision_id": str(d.id), "exception_id": str(e.id), "network": e.network_code, "exception_types": e.exception_types,
              "outcome": d.outcome.value, "reason_code": d.reason_code, "source": d.source.value, "decided_at": _iso(d.decided_at),
              "decided_by": who(d.decided_by_user_id), "submitted_by": who(d.submitted_by_user_id),
              "recommended_by": who(e.recommended_by_user_id), "ml_score": e.ml_score, "ml_model_version": e.ml_model_version}
             for d, e in rows]
    counts: dict[str, int] = {}
    for item in items:
        counts[item["outcome"]] = counts.get(item["outcome"], 0) + 1
    return {"count": len(items), "truncated": truncated, "by_outcome": counts, "items": items}


def _control_runs(session: Session, tenant_id: uuid.UUID, start: datetime, end: datetime) -> list[dict]:
    from openclear.domain.control_run import ControlRun

    rows = session.execute(select(ControlRun).where(ControlRun.tenant_id == tenant_id, ControlRun.started_at >= start,
                                                    ControlRun.started_at < end).order_by(ControlRun.started_at)).scalars()
    return [{"id": str(r.id), "kind": r.kind, "started_at": _iso(r.started_at), "passed": r.passed, "summary": r.summary,
             "scheduled": r.requested_by_user_id is None} for r in rows]


def build(session: Session, tenant_id: uuid.UUID, *, start: date, end: date, generated_by_user_id: uuid.UUID | None) -> dict:
    from openclear.domain.tenant import Tenant
    from openclear.services import controls_service

    period_start, period_end = _period(start, end)
    tenant = session.get(Tenant, tenant_id)
    controls = controls_service.summary(controls_service.status(session, tenant_id))
    return {
        "format": "openclear-evidence/1",
        "bank": {"id": str(tenant.id), "name": tenant.name, "slug": tenant.slug},
        "period": {"start": start.isoformat(), "end": end.isoformat()},
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "generated_by_user_id": str(generated_by_user_id) if generated_by_user_id else None,
        "system": _system(session),
        "rules": _rules(session, tenant_id),
        "models": _models(session, tenant_id),
        "settings": _settings(session, tenant_id),
        "audit": _audit(session, tenant_id, period_start, period_end),
        "decisions": _decisions(session, tenant_id, period_start, period_end),
        "control_runs": _control_runs(session, tenant_id, period_start, period_end),
        "controls": controls,
    }


def _public_key_pem() -> str:
    from cryptography.hazmat.primitives import serialization

    from openclear.auth.keys import load_public_key
    from openclear.config import get_settings

    key = load_public_key(get_settings().file_signing_public_key_path)
    return key.public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()


def sign(bundle: dict) -> dict:
    """The bundle with its signature: ECDSA P-256 over the SHA-256 of canonical(bundle)."""
    from openclear.bulk_import.signing import sign_digest

    digest = hashlib.sha256(canonical(bundle)).digest()
    return {"bundle": bundle, "signature": {
        "algorithm": "ECDSA-P256-SHA256", "signed": "sha256 of the bundle as JSON with sorted keys and no spaces (UTF-8)",
        "sha256": digest.hex(), "value": sign_digest(digest), "public_key": _public_key_pem()}}


def verify(signed: dict) -> bool:
    """Whether a JSON export is unaltered and signed by this installation's key."""
    from openclear.bulk_import.signing import verify_digest_signature

    digest = hashlib.sha256(canonical(signed["bundle"])).digest()
    return digest.hex() == signed["signature"]["sha256"] and verify_digest_signature(digest, signed["signature"]["value"])


def render_pdf(signed: dict) -> bytes:
    import weasyprint

    from openclear.web.templates import templates

    html = templates.env.get_template("evidence/report.html").render(b=signed["bundle"], sig=signed["signature"])
    return weasyprint.HTML(string=html, base_url="http://openclear.local/").write_pdf()


def record_export(session: Session, tenant_id: uuid.UUID, *, user_id: uuid.UUID | None, channel: str, start: date, end: date,
                  fmt: str, sha256: str) -> None:
    from openclear.services import audit_log_service

    audit_log_service.record_action(session, tenant_id, actor_user_id=user_id, channel=channel, action="audit.export",
                                    summary=f"Exported evidence for {start.isoformat()} to {end.isoformat()} ({fmt.upper()})",
                                    resource_type="tenant", resource_id=tenant_id,
                                    metadata={"start": start.isoformat(), "end": end.isoformat(), "format": fmt, "sha256": sha256})
