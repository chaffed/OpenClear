# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""The evidence export (COMPLIANCE_PLAN.md C3; 2023 Interagency Guidance on Third-Party
Relationships): one signed bundle an examiner or auditor can take away for a period.

It holds what the installation is running (its version and database revision, the rules,
plugins and models in force with digests of each), the bank's settings and security
groups, whether the action log verifies, segregation-of-duties breaches, changes and their
approvals, every decision in the period with who recommended and who decided it, the
model-risk test runs, security event counts and their delivery, and a live snapshot of the
controls (services/controls_service.py).

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
    from openclear.domain.ml_model import MlModel, MlModelStatus

    rows = session.execute(select(MlModel).where(MlModel.status == MlModelStatus.ACTIVE, MlModel.tenant_id == tenant_id)).scalars()
    return [{"id": str(m.id), "network": m.network_code, "version": m.version, "algorithm": m.algorithm,
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
            "security_groups": groups, "decision_cutoff": _cutoffs(session, tenant)}


def _cutoffs(session: Session, tenant) -> dict:
    """When decisions become final (UI_PLAN.md UI-D13): the bank's cut-offs, its closures, and
    each cut-off agreed with a customer, with when it was last set."""
    from openclear.domain.customer import Customer
    from openclear.domain.customer_disposition_setting import CustomerDispositionSetting
    from openclear.services import cutoff_service

    agreed = session.execute(
        select(Customer.customer_number, Customer.name, CustomerDispositionSetting.network_code, CustomerDispositionSetting.cutoff_time,
               CustomerDispositionSetting.updated_at)
        .join(Customer, Customer.id == CustomerDispositionSetting.customer_id)
        .where(CustomerDispositionSetting.tenant_id == tenant.id, CustomerDispositionSetting.cutoff_time.is_not(None))
        .order_by(Customer.customer_number, CustomerDispositionSetting.network_code)).all()
    return {"time_zone": tenant.time_zone, "cutoffs": dict(tenant.decision_cutoffs or {}),
            "min_review_minutes": tenant.cutoff_min_review_minutes,
            "closures": [{"day": c.closed_on.isoformat(), "name": c.name} for c in cutoff_service.closures(session, tenant.id)],
            "agreed_with_customers": [{"customer_number": number, "customer": name, "network": network, "cutoff": cutoff,
                                       "set_at": _iso(updated)} for number, name, network, cutoff, updated in agreed]}


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

    from sqlalchemy import func

    from openclear.domain.decision import DecisionRevision

    ids = [e.id for _d, e in rows]
    changes = dict(session.execute(select(DecisionRevision.exception_item_id, func.count(DecisionRevision.id))
                                   .where(DecisionRevision.exception_item_id.in_(ids)).group_by(DecisionRevision.exception_item_id)).all()) if ids else {}

    items = [{"decision_id": str(d.id), "exception_id": str(e.id), "network": e.network_code, "exception_types": e.exception_types,
              "outcome": d.outcome.value, "reason_code": d.reason_code, "source": d.source.value, "decided_at": _iso(d.decided_at),
              "locked_at": _iso(d.locked_at), "changes_before_cutoff": changes.get(e.id, 0),
              "decided_by": who(d.decided_by_user_id), "submitted_by": who(d.submitted_by_user_id),
              "recommended_by": who(e.recommended_by_user_id), "ml_score": e.ml_score, "ml_model_version": e.ml_model_version}
             for d, e in rows]
    counts: dict[str, int] = {}
    for item in items:
        counts[item["outcome"]] = counts.get(item["outcome"], 0) + 1
    return {"count": len(items), "truncated": truncated, "by_outcome": counts, "items": items}


UNAUTHORIZED_RETURN_CODES = ("R10", "R11", "R29", "R17")


def _ach_monitoring(session: Session, tenant_id: uuid.UUID, start: datetime, end: datetime) -> dict:
    """The period's ACH debits, exceptions and returns, shaped for a bank's fraud-monitoring file and
    its yearly Rules audit (REGULATORY_PLAN.md RG2; control OC-SI-10). Debits only: OpenClear doesn't
    monitor incoming credits, which the bank's own process covers (CUEC B-SI-4-02)."""
    import statistics

    from sqlalchemy import func

    from openclear.domain.ach_transaction import AchTransaction, AchTransactionType
    from openclear.domain.decision import Decision, DecisionOutcome
    from openclear.domain.exception_item import ExceptionItem

    debits = dict(session.execute(select(AchTransaction.sec_code, func.count()).where(
        AchTransaction.tenant_id == tenant_id, AchTransaction.transaction_type == AchTransactionType.DEBIT,
        AchTransaction.created_at >= start, AchTransaction.created_at < end).group_by(AchTransaction.sec_code)).all())
    exceptions = session.execute(select(func.count()).select_from(ExceptionItem).where(
        ExceptionItem.tenant_id == tenant_id, ExceptionItem.network_code == "ach", ExceptionItem.created_at >= start,
        ExceptionItem.created_at < end)).scalar_one()
    decided = session.execute(select(Decision, ExceptionItem, AchTransaction)
                              .join(ExceptionItem, ExceptionItem.id == Decision.exception_item_id)
                              .join(AchTransaction, AchTransaction.id == ExceptionItem.source_item_id).where(
        Decision.tenant_id == tenant_id, ExceptionItem.network_code == "ach", Decision.decided_at >= start, Decision.decided_at < end)).all()
    returns: dict[str, int] = {}
    unauthorized: dict[tuple[str, str], int] = {}
    minutes, late = [], 0
    for decision, exception, txn in decided:
        if decision.outcome == DecisionOutcome.RETURN:
            code = decision.return_transaction_code or "unset"
            returns[code] = returns.get(code, 0) + 1
            if code in UNAUTHORIZED_RETURN_CODES:
                unauthorized[(txn.originator_id, txn.originator_name)] = unauthorized.get((txn.originator_id, txn.originator_name), 0) + 1
        made, due = _aware(decision.decided_at), _aware(exception.decision_deadline)
        minutes.append((made - _aware(exception.created_at)).total_seconds() / 60)
        late += due is not None and made > due
    top = sorted(unauthorized.items(), key=lambda kv: -kv[1])[:50]
    return {
        "scope": "ACH debits on positive pay accounts; incoming credits aren't monitored by OpenClear (CUEC B-SI-4-02)",
        "debits_by_sec_code": debits, "exceptions": exceptions, "decisions": len(decided),
        "returns_by_reason_code": returns,
        "unauthorized_returns_by_originator": [{"originator_id": oid, "originator_name": name, "returns": n} for (oid, name), n in top],
        "minutes_to_decision": {"median": round(statistics.median(minutes), 1) if minutes else None,
                                "max": round(max(minutes), 1) if minutes else None},
        "decided_after_deadline": late,
    }


def _aware(value):
    return value.replace(tzinfo=timezone.utc) if value is not None and value.tzinfo is None else value


def _control_runs(session: Session, tenant_id: uuid.UUID, start: datetime, end: datetime) -> list[dict]:
    from openclear.domain.control_run import ControlRun

    rows = session.execute(select(ControlRun).where(ControlRun.tenant_id == tenant_id, ControlRun.started_at >= start,
                                                    ControlRun.started_at < end).order_by(ControlRun.started_at)).scalars()
    return [{"id": str(r.id), "kind": r.kind, "started_at": _iso(r.started_at), "passed": r.passed, "summary": r.summary,
             "scheduled": r.requested_by_user_id is None} for r in rows]


def _security_events(session: Session, tenant_id: uuid.UUID, start: datetime, end: datetime) -> dict:
    """How many security events the bank had in the period, by category, and where they
    were delivered (SIEM_PLAN.md S5). Event counts cover what's still kept
    (security_event_retention_days); the SIEM holds the rest."""
    from openclear.domain.tenant import Tenant
    from openclear.services.siem import delivery

    counts = delivery.counts_by_category(session, tenant_id, start, end)
    tenant = session.get(Tenant, tenant_id)
    return {
        "counts_by_category": counts, "total": sum(counts.values()),
        "feed_last_read_at": _iso(tenant.security_event_feed_read_at),
        "destinations": [{
            "receives": "this bank's events" if s.destination.tenant_id else "every bank's events (the installation's)",
            "destination": delivery.describe(s.destination), "delivered_total": s.destination.delivered_count,
            "last_success_at": _iso(s.destination.last_success_at), "failing_since": _iso(s.destination.failing_since),
            "last_error": s.destination.last_error, "waiting": s.backlog,
        } for s in delivery.status_for_bank(session, tenant_id)],
    }


def _support_access(session: Session, tenant_id: uuid.UUID, start: datetime, end: datetime) -> list[dict]:
    """Support access the bank granted that was open at any time in the period (UX_PLAN.md U8)."""
    from openclear.domain.support_access_grant import SupportAccessGrant
    from openclear.domain.user import User

    rows = session.execute(select(SupportAccessGrant).where(
        SupportAccessGrant.tenant_id == tenant_id, SupportAccessGrant.granted_at < end, SupportAccessGrant.expires_at >= start,
    ).order_by(SupportAccessGrant.granted_at)).scalars().all()
    ids = {r.support_user_id for r in rows} | {r.granted_by_user_id for r in rows} | {r.approved_by_user_id for r in rows if r.approved_by_user_id}
    emails = dict(session.execute(select(User.id, User.email).where(User.id.in_(ids))).all()) if ids else {}
    return [{"support_person": emails.get(r.support_user_id), "reason": r.reason, "write_access": r.write_access, "status": r.status,
             "granted_by": emails.get(r.granted_by_user_id), "approved_by": emails.get(r.approved_by_user_id),
             "granted_at": _iso(r.granted_at), "expires_at": _iso(r.expires_at), "ended_at": _iso(r.ended_at),
             "last_used_at": _iso(r.last_used_at)} for r in rows]


def _control_testing(session: Session, tenant_id: uuid.UUID, start: datetime, end: datetime) -> dict:
    """The period's control testing (CONTROLS_PLAN.md CT2 to CT5): each check's results (counts and
    the digest over their evidence digests), the deviations open in it, and the CUEC attestations
    accepted for periods overlapping it, with their signatures' digests."""
    from openclear.compliance import checks
    from openclear.domain.control_result import ControlDeviation, ControlTestResult
    from openclear.domain.cuec_attestation import CuecAttestation

    results = session.execute(select(ControlTestResult).where(
        ControlTestResult.tenant_id == tenant_id, ControlTestResult.recorded_at >= start, ControlTestResult.recorded_at < end)
        .order_by(ControlTestResult.check_key, ControlTestResult.recorded_at)).scalars().all()
    by_check: dict[str, list] = {}
    for r in results:
        by_check.setdefault(r.check_key, []).append(r)
    titles = {c.key: c.title for c in checks.registry()}
    deviations = session.execute(select(ControlDeviation).where(
        ControlDeviation.tenant_id == tenant_id, ControlDeviation.opened_at < end,
        (ControlDeviation.closed_at.is_(None)) | (ControlDeviation.closed_at >= start)).order_by(ControlDeviation.opened_at)).scalars().all()
    attestations = session.execute(select(CuecAttestation).where(
        CuecAttestation.tenant_id == tenant_id, CuecAttestation.status.in_(("accepted", "superseded")),
        CuecAttestation.period_end >= start.date(), CuecAttestation.period_start < end.date())
        .order_by(CuecAttestation.cuec_id, CuecAttestation.period_end)).scalars().all()
    return {
        "checks": [{"check": key, "title": titles.get(key, key), "results": len(rows),
                    "statuses": {s: sum(r.status == s for r in rows) for s in ("PASS", "WARN", "FAIL")},
                    "results_sha256": hashlib.sha256("".join(sorted(r.evidence_sha256 for r in rows)).encode()).hexdigest()}
                   for key, rows in by_check.items()],
        "deviations": [{"id": str(d.id), "check": d.check_key, "status": d.status, "opened_at": _iso(d.opened_at), "closed_at": _iso(d.closed_at),
                        "cause": d.cause, "response": d.response} for d in deviations],
        "attestations": [{"id": str(a.id), "cuec": a.cuec_id, "customer_id": str(a.customer_id) if a.customer_id else None,
                          "period": [a.period_start.isoformat(), a.period_end.isoformat()], "result": a.result, "method": a.method,
                          "status": a.status, "auto_accepted": a.auto_accepted, "sha256": a.sha256} for a in attestations],
    }


def _change_control(session: Session, tenant_id: uuid.UUID, start: datetime, end: datetime) -> list[dict]:
    """The period's settings changes that needed a second person (DUAL_CONTROL_PLAN.md): asked for,
    or decided, in the period, with both people and the values before and after."""
    from openclear.domain.change_request import ChangeRequest
    from openclear.domain.user import User

    rows = session.execute(select(ChangeRequest).where(ChangeRequest.tenant_id == tenant_id, or_(
        (ChangeRequest.requested_at >= start) & (ChangeRequest.requested_at < end),
        (ChangeRequest.decided_at >= start) & (ChangeRequest.decided_at < end))).order_by(ChangeRequest.requested_at)).scalars().all()
    ids = {c.requested_by_user_id for c in rows} | {c.decided_by_user_id for c in rows}
    emails = {u.id: u.email for u in session.execute(select(User).where(User.id.in_([i for i in ids if i]))).scalars()} if ids else {}
    return [{"id": str(c.id), "kind": c.kind, "summary": c.summary, "scope": c.scope, "customer_id": str(c.customer_id) if c.customer_id else None,
             "status": c.status, "requested_by": emails.get(c.requested_by_user_id) or c.requested_with, "requested_at": _iso(c.requested_at),
             "decided_by": emails.get(c.decided_by_user_id), "decided_at": _iso(c.decided_at), "reason": c.decided_reason,
             "before": c.before, "after": c.after} for c in rows]


def _service_provider(session: Session) -> dict:
    """The service provider's side (DUAL_CONTROL_PLAN.md DC-D14, DC6): its action log's last
    verification (the log itself is the provider's, not the bank's) and the installation's
    security-relevant settings, as digests only, against what an operator last acknowledged."""
    from openclear.services import installation_settings, platform_log_service

    run = platform_log_service.latest_verification(session)
    now = installation_settings.current()
    row = installation_settings.acknowledged(session)
    return {
        "action_log": {"verified_at": _iso(run.verified_at), "intact": run.intact, "entries_checked": run.checked,
                       "broken_at_sequence": run.broken_at_sequence} if run else None,
        "settings": {"digest": installation_settings.digest(now),
                     "acknowledged_digest": row.digest if row else None, "acknowledged_at": _iso(row.acknowledged_at) if row else None,
                     "changed_since_acknowledged": sorted(k for k, v in now.items() if row and row.settings.get(k) != v)},
    }


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
        "ach_monitoring": _ach_monitoring(session, tenant_id, period_start, period_end),
        "control_runs": _control_runs(session, tenant_id, period_start, period_end),
        "security_events": _security_events(session, tenant_id, period_start, period_end),
        "support_access": _support_access(session, tenant_id, period_start, period_end),
        "control_testing": _control_testing(session, tenant_id, period_start, period_end),
        "change_control": _change_control(session, tenant_id, period_start, period_end),
        "service_provider": _service_provider(session),
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
