# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""CUEC mappings and attestations (CONTROLS_PLAN.md CT4; domain/cuec_attestation.py).

**Mappings.** A bank (or a customer, for its own CUECs) records which of its controls cover each
CUEC, one at a time or from the cuecs.csv it filled in.

**Attestations.** At the end of each period (Tenant.attestation_period) OpenClear proposes one
for each automated CUEC of the bank's (and the host's, when the bank attests them) from its
check's results (CT-D6):
- *operated*: the check ran at least 95% of its scheduled times, no deviation lasted longer than
  the bank's grace period, and every population test passed;
- *operated with exceptions*: covered, but with such deviations or failed population tests,
  each listed;
- *not tested*: under 95% coverage.
The raw numbers always go with it, so an auditor can apply their own threshold. OpenClear only
proposes (CT-D7): the control owner accepts, unless a mapping accepts automatically. Assisted and
attested CUECs, and customers' own, are attested by a person.

**Signing.** An accepted attestation is canonical JSON signed with the installation's
file-signing key (ECDSA P-256, as the evidence export), carrying the digests of the results it
rests on. It can't be edited; a correction is a new attestation that supersedes it. Under
`attest_second_person`, whoever prepared a manual attestation can't accept it (CT-D9)."""

import csv
import hashlib
import io
import uuid
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from openclear.compliance import cuecs
from openclear.domain.cuec_attestation import CuecAttestation, CuecMapping
from openclear.domain.tenant import Tenant

PERIODS = ("monthly", "quarterly", "yearly")
RESULTS = ("operated", "operated_with_exceptions", "not_operated", "not_applicable", "not_tested")
MANUAL_RESULTS = ("operated", "operated_with_exceptions", "not_operated", "not_applicable")
COVERAGE_NEEDED = 0.95


class AttestationError(ValueError):
    """The message is fit to show."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


# --- Periods -----------------------------------------------------------------------------------


def period_containing(kind: str, day: date) -> tuple[date, date]:
    """The period of this kind that contains `day`: (first day, last day), both inclusive."""
    if kind == "monthly":
        start = day.replace(day=1)
    elif kind == "quarterly":
        start = day.replace(month=(day.month - 1) // 3 * 3 + 1, day=1)
    elif kind == "yearly":
        start = day.replace(month=1, day=1)
    else:
        raise AttestationError("A period is monthly, quarterly or yearly.")
    months = {"monthly": 1, "quarterly": 3, "yearly": 12}[kind]
    year, month = divmod(start.month - 1 + months, 12)
    return start, date(start.year + year, month + 1, 1) - timedelta(days=1)


def previous_period(kind: str, today: date) -> tuple[date, date]:
    start, _end = period_containing(kind, today)
    return period_containing(kind, start - timedelta(days=1))


# --- Scope -------------------------------------------------------------------------------------


def parties_for(customer_id: uuid.UUID | None) -> tuple[str, ...]:
    """The CUEC parties a scope attests: a customer its own; the bank its own, and the host's when
    the bank hosts OpenClear itself."""
    from openclear.config import get_settings

    if customer_id is not None:
        return ("customer",)
    return ("bank", "host") if get_settings().host_cuecs_attested_by == "bank" else ("bank",)


def _scoped(stmt, model, tenant_id, customer_id):
    stmt = stmt.where(model.tenant_id == tenant_id)
    return stmt.where(model.customer_id == customer_id) if customer_id else stmt.where(model.customer_id.is_(None))


# --- Mappings ----------------------------------------------------------------------------------


def mappings(session: Session, tenant_id: uuid.UUID, customer_id: uuid.UUID | None, cuec_id: str | None = None) -> list[CuecMapping]:
    stmt = _scoped(select(CuecMapping), CuecMapping, tenant_id, customer_id)
    if cuec_id:
        stmt = stmt.where(CuecMapping.cuec_id == cuec_id)
    return list(session.execute(stmt.order_by(CuecMapping.cuec_id, CuecMapping.control_ref)).scalars())


def _cuec(cuec_id: str, customer_id: uuid.UUID | None) -> cuecs.Cuec:
    found = cuecs.by_id().get(cuec_id)
    if found is None or found.withdrawn or found.party not in parties_for(customer_id) + (("host",) if customer_id is None else ()):
        raise AttestationError("No such CUEC for you.")
    return found


def add_mapping(session: Session, tenant_id: uuid.UUID, customer_id: uuid.UUID | None, cuec_id: str, *, control_ref: str,
                control_name: str = "", owner: str = "", link: str = "", auto_accept: bool = False,
                user_id: uuid.UUID | None = None) -> CuecMapping:
    _cuec(cuec_id, customer_id)
    control_ref = (control_ref or "").strip()
    if not control_ref:
        raise AttestationError("Give your control's id.")
    if link and not link.lower().startswith(("https://", "http://")):
        raise AttestationError("A link starts with https://.")
    mapping = CuecMapping(tenant_id=tenant_id, customer_id=customer_id, cuec_id=cuec_id, control_ref=control_ref[:100],
                          control_name=(control_name or "").strip()[:300] or None, owner=(owner or "").strip()[:200] or None,
                          link=(link or "").strip()[:1000] or None, auto_accept=bool(auto_accept), inventory_version=cuecs.VERSION,
                          created_by_user_id=user_id)
    session.add(mapping)
    session.flush()
    return mapping


def set_mappings(session: Session, tenant_id: uuid.UUID, customer_id: uuid.UUID | None, cuec_id: str, wanted: list[dict], *,
                 user_id: uuid.UUID | None) -> tuple[list[CuecMapping], dict]:
    """Make a CUEC's mappings exactly `wanted` (each {control_ref, control_name, owner, link,
    auto_accept}), matched on control_ref: new ones added, missing ones removed, the rest updated.
    Returns the mappings and what changed: {added, removed, updated: [control_ref], auto_accept: [(mapping, on)]}."""
    _cuec(cuec_id, customer_id)
    refs = [(w.get("control_ref") or "").strip() for w in wanted]
    if any(not r for r in refs):
        raise AttestationError("Give each mapping your control's id.")
    if len(set(refs)) != len(refs):
        raise AttestationError("Each of your controls once.")
    existing = {m.control_ref: m for m in mappings(session, tenant_id, customer_id, cuec_id)}
    changes: dict = {"added": [], "removed": [], "updated": [], "auto_accept": []}
    for ref, mapping in existing.items():
        if ref not in refs:
            session.delete(mapping)
            changes["removed"].append(ref)
    for ref, item in zip(refs, wanted):
        link = (item.get("link") or "").strip()
        if link and not link.lower().startswith(("https://", "http://")):
            raise AttestationError("A link starts with https://.")
        mapping = existing.get(ref)
        if mapping is None:
            mapping = add_mapping(session, tenant_id, customer_id, cuec_id, control_ref=ref, control_name=item.get("control_name") or "",
                                  owner=item.get("owner") or "", link=link, user_id=user_id)
            changes["added"].append(ref)
        else:
            fields = {"control_name": (item.get("control_name") or "").strip()[:300] or None,
                      "owner": (item.get("owner") or "").strip()[:200] or None, "link": link[:1000] or None}
            if any(getattr(mapping, k) != v for k, v in fields.items()):
                for k, v in fields.items():
                    setattr(mapping, k, v)
                changes["updated"].append(ref)
        on = bool(item.get("auto_accept"))
        if mapping.auto_accept != on:
            mapping.auto_accept = on
            changes["auto_accept"].append((mapping, on))
    session.flush()
    return mappings(session, tenant_id, customer_id, cuec_id), changes


def store_files(tenant_id: uuid.UUID, uploads: list[tuple[str, bytes]]) -> list[dict]:
    """Evidence files for a manual attestation, kept read-only: [{name, sha256, size, storage_ref}]."""
    from openclear import storage

    files = []
    for name, data in uploads:
        name = (name or "evidence")[-200:]
        ref = storage.put("audit_archives", f"{tenant_id}/attestations/{uuid.uuid4().hex}-{name}", data, read_only=True)
        files.append({"name": name, "sha256": hashlib.sha256(data).hexdigest(), "size": len(data), "storage_ref": ref})
    return files


def get_mapping(session: Session, tenant_id: uuid.UUID, customer_id: uuid.UUID | None, mapping_id: uuid.UUID) -> CuecMapping | None:
    found = session.get(CuecMapping, mapping_id)
    return found if found is not None and found.tenant_id == tenant_id and found.customer_id == customer_id else None


def import_csv(session: Session, tenant_id: uuid.UUID, customer_id: uuid.UUID | None, content: bytes, *,
               user_id: uuid.UUID | None) -> tuple[int, list[str]]:
    """Mappings from the cuecs.csv a bank filled in (its `your_control_id` and `your_control_owner`
    columns, and optionally `your_control_name` and `your_control_link`). Rows left blank are
    skipped; a mapping already there for the same control isn't added twice. Returns (added, problems)."""
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise AttestationError("The file must be UTF-8 CSV, as cuecs.csv is.") from None
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames or "id" not in reader.fieldnames or "your_control_id" not in reader.fieldnames:
        raise AttestationError("Use cuecs.csv with its id and your_control_id columns.")
    existing = {(m.cuec_id, m.control_ref) for m in mappings(session, tenant_id, customer_id)}
    added, problems = 0, []
    for number, row in enumerate(reader, start=2):
        ref = (row.get("your_control_id") or "").strip()
        if not ref:
            continue
        cuec_id = (row.get("id") or "").strip()
        if (cuec_id, ref) in existing:
            continue
        try:
            add_mapping(session, tenant_id, customer_id, cuec_id, control_ref=ref, owner=row.get("your_control_owner") or "",
                        control_name=row.get("your_control_name") or "", link=row.get("your_control_link") or "", user_id=user_id)
        except AttestationError as exc:
            problems.append(f"Line {number} ({cuec_id or 'no id'}): {exc}")
            continue
        existing.add((cuec_id, ref))
        added += 1
    return added, problems


# --- Automated evaluation (CT-D6) --------------------------------------------------------------


def evaluate(session: Session, tenant_id: uuid.UUID, cuec: cuecs.Cuec, start: date, end: date) -> tuple[str, dict]:
    """The result OpenClear proposes for an automated CUEC over [start, end], and the numbers behind it."""
    from openclear.compliance import checks
    from openclear.domain.control_result import ControlDeviation, ControlTestResult

    check = checks.by_key().get(cuec.check or "")
    if check is None:
        return "not_tested", {"reason": f"The check {cuec.check!r} isn't live yet."}
    begin = datetime.combine(start, time.min, tzinfo=timezone.utc)
    finish = datetime.combine(end + timedelta(days=1), time.min, tzinfo=timezone.utc)
    days = (end - start).days + 1
    results = session.execute(select(ControlTestResult).where(
        ControlTestResult.tenant_id == tenant_id, ControlTestResult.check_key == check.key, ControlTestResult.started_by == "schedule",
        ControlTestResult.recorded_at >= begin, ControlTestResult.recorded_at < finish)).scalars().all()
    expected = days * (24 if check.schedule == "hourly" else 1)
    coverage = min(1.0, len(results) / expected) if expected else 0.0
    grace = timedelta(hours=session.get(Tenant, tenant_id).control_grace_hours)
    deviations = session.execute(select(ControlDeviation).where(
        ControlDeviation.tenant_id == tenant_id, ControlDeviation.check_key == check.key, ControlDeviation.opened_at < finish,
        (ControlDeviation.closed_at.is_(None)) | (ControlDeviation.closed_at >= begin))).scalars().all()

    def lasted(d) -> timedelta:
        """How long it lasted in all (to the period's end if it's still open), not only within the
        period: one that outlasted the grace period counts, whichever period it started in."""
        opened = d.opened_at if d.opened_at.tzinfo else d.opened_at.replace(tzinfo=timezone.utc)
        closed = d.closed_at or finish
        return (closed if closed.tzinfo else closed.replace(tzinfo=timezone.utc)) - opened

    long_ones = [d for d in deviations if lasted(d) > grace]
    failed_population = [r for r in results if check.kind == "population" and r.status != "PASS"]
    evidence = {
        "check": check.key, "schedule": check.schedule, "kind": check.kind, "period": [start.isoformat(), end.isoformat()],
        "scheduled_runs_expected": expected, "scheduled_runs_recorded": len(results), "coverage": round(coverage, 4),
        "coverage_needed": COVERAGE_NEEDED, "grace_hours": int(grace.total_seconds() // 3600),
        "statuses": {s: sum(r.status == s for r in results) for s in ("PASS", "WARN", "FAIL")},
        "deviations": [{"id": str(d.id), "status": d.status, "opened_at": d.opened_at.isoformat(),
                        "closed_at": d.closed_at.isoformat() if d.closed_at else None, "hours": round(lasted(d).total_seconds() / 3600, 1),
                        "beyond_grace": d in long_ones, "responded": bool(d.response)} for d in deviations],
        "population_tests_failed": len(failed_population),
        "result_digests": sorted(r.evidence_sha256 for r in results),
    }
    evidence["results_sha256"] = hashlib.sha256("".join(evidence["result_digests"]).encode()).hexdigest()
    if coverage < COVERAGE_NEEDED:
        return "not_tested", evidence
    if long_ones or failed_population:
        return "operated_with_exceptions", evidence
    return "operated", evidence


# --- Preparing, accepting, rejecting -----------------------------------------------------------


def attestations(session: Session, tenant_id: uuid.UUID, customer_id: uuid.UUID | None, *, cuec_id: str | None = None,
                 limit: int = 500) -> list[CuecAttestation]:
    stmt = _scoped(select(CuecAttestation), CuecAttestation, tenant_id, customer_id)
    if cuec_id:
        stmt = stmt.where(CuecAttestation.cuec_id == cuec_id)
    return list(session.execute(stmt.order_by(CuecAttestation.period_end.desc(), CuecAttestation.prepared_at.desc()).limit(limit)).scalars())


def installation_attestations(session: Session, *, cuec_id: str | None = None) -> list[CuecAttestation]:
    """The host CUECs' attestations made once for the installation (CT-D12)."""
    stmt = select(CuecAttestation).where(CuecAttestation.tenant_id.is_(None))
    if cuec_id:
        stmt = stmt.where(CuecAttestation.cuec_id == cuec_id)
    return list(session.execute(stmt.order_by(CuecAttestation.period_end.desc())).scalars())


def get_attestation(session: Session, tenant_id: uuid.UUID | None, customer_id: uuid.UUID | None, attestation_id: uuid.UUID) -> CuecAttestation | None:
    found = session.get(CuecAttestation, attestation_id)
    if found is None:
        return None
    if found.tenant_id is None and found.customer_id is None:  # the installation's: readable by every bank
        return found
    return found if found.tenant_id == tenant_id and found.customer_id == customer_id else None


def _open_for(session: Session, tenant_id, customer_id, cuec_id: str, start: date, end: date) -> CuecAttestation | None:
    return session.execute(_scoped(select(CuecAttestation), CuecAttestation, tenant_id, customer_id).where(
        CuecAttestation.cuec_id == cuec_id, CuecAttestation.period_start == start, CuecAttestation.period_end == end,
        CuecAttestation.status.in_(("prepared", "accepted")))).scalars().first()


def prepare_automated(session: Session, tenant_id: uuid.UUID, kind: str, start: date, end: date) -> list[CuecAttestation]:
    """Propose an attestation for each automated CUEC the bank attests, for the period, unless one
    is already prepared or accepted. A CUEC whose mapping accepts automatically is accepted and
    signed at once. Returns those made."""
    made = []
    for cuec in cuecs.CUECS:
        if cuec.withdrawn or cuec.evidence != "automated" or cuec.party not in parties_for(None):
            continue
        if _open_for(session, tenant_id, None, cuec.id, start, end) is not None:
            continue
        result, evidence = evaluate(session, tenant_id, cuec, start, end)
        attestation = CuecAttestation(tenant_id=tenant_id, customer_id=None, cuec_id=cuec.id, period_kind=kind, period_start=start,
                                      period_end=end, result=result, method="automated", evidence=evidence, files=[], status="prepared",
                                      prepared_at=_now(), inventory_version=cuecs.VERSION)
        session.add(attestation)
        session.flush()
        if any(m.auto_accept for m in mappings(session, tenant_id, None, cuec.id)):
            _accept(session, attestation, user_id=None, auto=True)
        made.append(attestation)
    return made


def record_manual(session: Session, tenant_id: uuid.UUID, customer_id: uuid.UUID | None, cuec_id: str, *, start: date, end: date,
                  result: str, statement: str, files: list[dict] | None = None, user_id: uuid.UUID) -> CuecAttestation:
    cuec = _cuec(cuec_id, customer_id)
    if cuec.party not in parties_for(customer_id):
        raise AttestationError("This installation's platform staff attest the host's CUECs.")
    if result not in MANUAL_RESULTS:
        raise AttestationError("Choose a result.")
    statement = (statement or "").strip()
    if not statement:
        raise AttestationError("Say what you attest, and how you know.")
    if end < start or end > _now().date():
        raise AttestationError("Choose a period that has ended.")
    kind = session.get(Tenant, tenant_id).attestation_period
    evidence = {"related_check": cuec.check, "statement_length": len(statement)}
    if cuec.check:
        from openclear.services import control_results_service

        latest = control_results_service.latest(session, tenant_id, cuec.check)
        if latest is not None:
            evidence["related_check_latest"] = {"status": latest.status, "summary": latest.summary,
                                                "recorded_at": latest.recorded_at.isoformat(), "evidence_sha256": latest.evidence_sha256}
    attestation = CuecAttestation(tenant_id=tenant_id, customer_id=customer_id, cuec_id=cuec_id, period_kind=kind, period_start=start,
                                  period_end=end, result=result, method="manual", statement=statement[:8000], evidence=evidence,
                                  files=files or [], status="prepared", prepared_by_user_id=user_id, prepared_at=_now(),
                                  inventory_version=cuecs.VERSION)
    session.add(attestation)
    session.flush()
    return attestation


def accept(session: Session, attestation: CuecAttestation, *, user_id: uuid.UUID) -> CuecAttestation:
    if attestation.status != "prepared":
        raise AttestationError("Only a prepared attestation can be accepted.")
    tenant = session.get(Tenant, attestation.tenant_id) if attestation.tenant_id else None
    if tenant is not None and tenant.attest_second_person and attestation.method == "manual" and attestation.prepared_by_user_id == user_id:
        raise AttestationError("Your bank asks for a second person: someone else must accept what you prepared.")
    return _accept(session, attestation, user_id=user_id, auto=False)


def _accept(session: Session, attestation: CuecAttestation, *, user_id: uuid.UUID | None, auto: bool) -> CuecAttestation:
    previous = session.execute(select(CuecAttestation).where(
        CuecAttestation.tenant_id == attestation.tenant_id if attestation.tenant_id else CuecAttestation.tenant_id.is_(None),
        CuecAttestation.customer_id == attestation.customer_id if attestation.customer_id else CuecAttestation.customer_id.is_(None),
        CuecAttestation.cuec_id == attestation.cuec_id, CuecAttestation.period_start == attestation.period_start,
        CuecAttestation.period_end == attestation.period_end, CuecAttestation.status == "accepted",
        CuecAttestation.id != attestation.id)).scalars().first()
    if previous is not None:
        previous.status = "superseded"
        attestation.supersedes_id = previous.id
    attestation.status, attestation.accepted_by_user_id, attestation.accepted_at, attestation.auto_accepted = "accepted", user_id, _now(), auto
    _sign(session, attestation)
    session.flush()
    return attestation


def reject(session: Session, attestation: CuecAttestation, *, reason: str, user_id: uuid.UUID) -> CuecAttestation:
    if attestation.status != "prepared":
        raise AttestationError("Only a prepared attestation can be rejected.")
    reason = (reason or "").strip()
    if not reason:
        raise AttestationError("Say why.")
    attestation.status, attestation.rejected_reason, attestation.rejected_by_user_id = "rejected", reason[:2000], user_id
    session.flush()
    return attestation


# --- Signing -----------------------------------------------------------------------------------


def body(session: Session, attestation: CuecAttestation) -> dict:
    """What the signature covers."""
    from openclear.domain.user import User

    def email(user_id):
        return session.get(User, user_id).email if user_id else None

    cuec = cuecs.by_id()[attestation.cuec_id]
    return {
        "attestation_id": str(attestation.id), "bank": str(attestation.tenant_id) if attestation.tenant_id else None,
        "customer": str(attestation.customer_id) if attestation.customer_id else None,
        "cuec": {"id": cuec.id, "title": cuec.title, "party": cuec.party, "nist": cuec.nist, "oscal_id": cuec.oscal_id,
                 "inventory_version": attestation.inventory_version},
        "mappings": [{"control": m.control_ref, "name": m.control_name, "owner": m.owner}
                     for m in (mappings(session, attestation.tenant_id, attestation.customer_id, attestation.cuec_id)
                               if attestation.tenant_id else [])],
        "period": {"kind": attestation.period_kind, "start": attestation.period_start.isoformat(), "end": attestation.period_end.isoformat()},
        "result": attestation.result, "method": attestation.method, "statement": attestation.statement, "evidence": attestation.evidence,
        "files": attestation.files, "prepared_by": email(attestation.prepared_by_user_id) or "OpenClear",
        "accepted_by": email(attestation.accepted_by_user_id) or ("OpenClear, by an auto-accepting mapping" if attestation.auto_accepted else None),
        "accepted_at": attestation.accepted_at.isoformat() if attestation.accepted_at else None,
        "supersedes": str(attestation.supersedes_id) if attestation.supersedes_id else None,
    }


def _sign(session: Session, attestation: CuecAttestation) -> None:
    from openclear.bulk_import.signing import sign_digest
    from openclear.services.evidence_service import canonical

    signed = body(session, attestation)
    digest = hashlib.sha256(canonical(signed)).digest()
    attestation.signed_body, attestation.sha256, attestation.signature = signed, digest.hex(), sign_digest(digest)


def verify(attestation: CuecAttestation) -> bool:
    """Whether the signed body is untouched, with only the installation's public key."""
    from openclear.bulk_import.signing import verify_digest_signature
    from openclear.services.evidence_service import canonical

    if not attestation.signed_body or not attestation.signature:
        return False
    digest = hashlib.sha256(canonical(attestation.signed_body)).digest()
    return digest.hex() == attestation.sha256 and verify_digest_signature(digest, attestation.signature)


def export(attestation: CuecAttestation) -> dict:
    """The signed attestation as a file: its body, digest, signature and the public key."""
    from openclear.services.evidence_service import _public_key_pem

    return {"attestation": attestation.signed_body, "signature": {"algorithm": "ECDSA P-256 over SHA-256 of the canonical JSON",
                                                                "sha256": attestation.sha256, "value": attestation.signature,
                                                                "public_key": _public_key_pem()}}


# --- The period job ----------------------------------------------------------------------------


def prepare_due(session: Session, today: date | None = None) -> int:
    """Nightly: for each bank whose period ended yesterday, propose that period's attestations."""
    from openclear.db.tenancy import bind_session_to_tenant

    today = today or _now().date()
    made = 0
    for tenant in session.execute(select(Tenant).where(Tenant.is_active.is_(True), Tenant.is_sandbox.is_(False))).scalars().all():
        start, end = previous_period(tenant.attestation_period, today)
        if end != today - timedelta(days=1):
            continue
        bind_session_to_tenant(session, tenant.id)
        prepared = prepare_automated(session, tenant.id, tenant.attestation_period, start, end)
        announce(session, tenant.id, prepared, channel="system")
        session.commit()
        made += len(prepared)
    return made


def announce(session: Session, tenant_id: uuid.UUID, made: list[CuecAttestation], *, channel: str, user_id: uuid.UUID | None = None) -> None:
    """Webhooks for proposed and accepted attestations, so a GRC tool can take them at once, and
    the action log for those accepted automatically."""
    from openclear.services import audit_log_service, webhook_service

    for a in made:
        data = {"attestation_id": str(a.id), "cuec_id": a.cuec_id, "period_start": a.period_start.isoformat(),
                "period_end": a.period_end.isoformat(), "result": a.result, "status": a.status}
        webhook_service.emit(session, tenant_id, "compliance.attestation.prepared", data)
        if a.status == "accepted":
            webhook_service.emit(session, tenant_id, "compliance.attestation.accepted", {**data, "sha256": a.sha256})
            audit_log_service.record_action(session, tenant_id, actor_user_id=user_id, channel=channel, action="compliance.attestation.accepted",
                                            summary=f"{a.cuec_id} for {a.period_start} to {a.period_end}: {a.result}, accepted by an "
                                                    "auto-accepting mapping", resource_type="cuec_attestation", resource_id=a.id)


def announce_accepted(session: Session, tenant_id: uuid.UUID, attestation: CuecAttestation) -> None:
    """The webhook for an attestation a person accepted."""
    from openclear.services import webhook_service

    webhook_service.emit(session, tenant_id, "compliance.attestation.accepted", {
        "attestation_id": str(attestation.id), "cuec_id": attestation.cuec_id, "period_start": attestation.period_start.isoformat(),
        "period_end": attestation.period_end.isoformat(), "result": attestation.result, "status": attestation.status,
        "sha256": attestation.sha256})


def record_installation(session: Session, cuec_id: str, *, start: date, end: date, result: str, statement: str,
                        user_id: uuid.UUID) -> CuecAttestation:
    """A host CUEC attested once for the whole installation by its platform staff (CT-D12), accepted
    and signed at once; every bank sees it read-only. Only from a session bound to no bank."""
    cuec = cuecs.by_id().get(cuec_id)
    if cuec is None or cuec.party != "host" or cuec.withdrawn:
        raise AttestationError("Only the host's CUECs are attested for the installation.")
    if result not in MANUAL_RESULTS:
        raise AttestationError("Choose a result.")
    statement = (statement or "").strip()
    if not statement:
        raise AttestationError("Say what you attest, and how you know.")
    if end < start or end > _now().date():
        raise AttestationError("Choose a period that has ended.")
    attestation = CuecAttestation(tenant_id=None, customer_id=None, cuec_id=cuec_id, period_kind="custom", period_start=start,
                                  period_end=end, result=result, method="manual", statement=statement[:8000], evidence={}, files=[],
                                  status="prepared", prepared_by_user_id=user_id, prepared_at=_now(), inventory_version=cuecs.VERSION)
    session.add(attestation)
    session.flush()
    return _accept(session, attestation, user_id=user_id, auto=False)
