# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Compliance for GRC tools (CONTROLS_PLAN.md CT1, CT5): the control and CUEC inventories, with
their NIST CSF 2.0 crosswalk (CT6); each control's test results and deviations; CUEC mappings,
the evidence behind a CUEC, and attestations (CT4), recorded, accepted and pulled signed; and
OSCAL documents (services/oscal_service.py). docs/guides/grc.md shows how a GRC tool uses them.

Reading needs `compliance:read`, which a read-only API client keeps; mappings
`compliance:manage`; recording, accepting and rejecting attestations `compliance:attest`. A
customer's own session reaches only its own CUECs, mappings and attestations. Lists page as the
others do, and with `updated_since` and `after_id` return only what changed, oldest change first.
`GET /api/v1/controls/status` stays as it is (CT-D14)."""

import csv
import io
import json
import uuid
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, Response, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from openclear.api.v1.files import file_response
from openclear.api.v1.pagination import Page, Since, changed_since, page_params, set_page_headers, updated_since_param
from openclear.auth.deps import get_current_context, require_permission
from openclear.compliance import controls, cuecs
from openclear.db.session import get_db
from openclear.db.tenancy import TenantContext
from openclear.services import attestation_service, audit_log_service, controls_service, oscal_service
from openclear.services.attestation_service import AttestationError

router = APIRouter(prefix="/compliance", tags=["compliance"])

Status = Literal["PASS", "WARN", "FAIL"]


class NistControl(BaseModel):
    id: str
    title: str
    oscal_id: str


class CsfSubcategory(BaseModel):
    id: str  # PR.AA-05
    title: str


class ControlCheck(BaseModel):
    key: str
    status: Literal["live", "planned"]
    title: str | None  # a live check's title on the Controls page
    result: Status | None  # a live check's status now; None for a planned one
    detail: str | None


class InventoryControl(BaseModel):
    id: str
    family: str
    title: str
    statement: str
    nist: list[NistControl]
    type: Literal["preventive", "detective", "corrective"]
    nature: Literal["built_in", "configurable"]
    checks: list[ControlCheck]
    csf: list[CsfSubcategory]  # the NIST CSF 2.0 subcategories NIST maps to its 800-53 controls
    soc2: list[str] | None  # the Trust Services Criteria AICPA maps to them (CC6.1); null when this installation has no SOC 2 mapping
    cuecs: list[str]
    where: str
    withdrawn: bool
    status: Status | None  # the worst of its live checks; None when it has none yet


class ControlInventory(BaseModel):
    version: str
    catalog: str
    checked_at: str
    controls: list[InventoryControl]


class InventoryCuec(BaseModel):
    id: str
    party: Literal["bank", "customer", "host"]
    nist: str
    nist_title: str
    oscal_id: str
    related: list[str]
    title: str
    statement: str
    complements: str
    evidence: Literal["automated", "assisted", "attested"]
    check: str | None
    check_status: Literal["live", "planned"] | None
    frequency: str
    csf: list[CsfSubcategory]  # the NIST CSF 2.0 subcategories NIST maps to its 800-53 control
    soc2: list[str] | None  # the Trust Services Criteria AICPA maps to it; null when this installation has no SOC 2 mapping
    where: str
    withdrawn: bool
    controls: list[str]  # the OpenClear controls that rely on it


class CuecInventory(BaseModel):
    version: str
    catalog: str
    cuecs: list[InventoryCuec]


_ORDER = {"PASS": 0, "WARN": 1, "FAIL": 2}


def inventory(live: list[controls_service.Control]) -> list[dict]:
    """Each control with its checks' results now (from controls_service), worst first among them."""
    results = {c.key: c for c in live}
    out = []
    for control in controls.CONTROLS:
        data = control.as_dict()
        for check in data["checks"]:
            found = results.get(check["key"])
            check["title"], check["result"], check["detail"] = (found.title, found.status, found.detail) if found else (None, None, None)
        seen = [c["result"] for c in data["checks"] if c["result"]]
        data["status"] = max(seen, key=_ORDER.__getitem__) if seen else None
        out.append(data)
    return out


@router.get("/controls", response_model=ControlInventory)
def control_inventory(db: Session = Depends(get_db), ctx: TenantContext = Depends(require_permission("audit_log:read"))) -> dict:
    """OpenClear's control inventory: each control with its NIST SP 800-53 controls, its checks
    (each live check's status for this bank now), the NIST CSF 2.0 subcategories NIST maps to
    those controls, and the CUECs it relies on. `status` is the worst of its live checks, or null
    when none of its checks is live yet."""
    if ctx.customer_id is not None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Controls are for the bank's own staff.")
    live = controls_service.status(db, ctx.tenant_id)
    return {"version": controls.VERSION, "catalog": cuecs.NIST_CATALOG, "checked_at": controls_service.summary(live)["checked_at"],
            "controls": inventory(live)}


@router.get("/cuecs", response_model=CuecInventory)
def cuec_inventory(party: Literal["bank", "customer", "host"] | None = Query(None),
                   ctx: TenantContext = Depends(get_current_context)) -> dict:
    """The complementary user entity controls: what OpenClear's controls rely on the bank, its
    customers and whoever runs OpenClear to do, each filed under its NIST SP 800-53 control (with
    the CSF 2.0 subcategories NIST maps to it), with the OpenClear controls that rely on it. A
    customer's own session sees only the customer's. Filter with `party`."""
    if ctx.customer_id is not None:
        if party not in (None, "customer"):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "A customer sees only its own CUECs.")
        party = "customer"
    relied_on: dict[str, list[str]] = {}
    for control in controls.CONTROLS:
        for cuec_id in control.cuecs:
            relied_on.setdefault(cuec_id, []).append(control.id)
    rows = [{**c.as_dict(), "controls": relied_on.get(c.id, [])} for c in cuecs.CUECS if party is None or c.party == party]
    return {"version": cuecs.VERSION, "catalog": cuecs.NIST_CATALOG, "cuecs": rows}


# --- CT5: results, mappings, evidence, attestations, OSCAL --------------------------------------

_read = require_permission("compliance:read")
_manage = require_permission("compliance:manage")
_attest = require_permission("compliance:attest")
FILE_LIMIT = 10 * 1024 * 1024
Result = Literal["operated", "operated_with_exceptions", "not_operated", "not_applicable", "not_tested"]
ManualResult = Literal["operated", "operated_with_exceptions", "not_operated", "not_applicable"]


class TestResultRead(BaseModel):
    id: uuid.UUID
    check_key: str
    status: Status
    summary: str
    evidence: dict[str, Any]
    evidence_sha256: str  # SHA-256 of the evidence as compact, key-sorted JSON
    period_start: datetime | None  # a population check's period; null for a state check
    period_end: datetime | None
    started_by: Literal["schedule", "view", "person"]
    recorded_at: datetime


class DeviationRead(BaseModel):
    id: uuid.UUID
    check_key: str
    status: Literal["WARN", "FAIL"]  # the worst it reached
    opened_at: datetime
    closed_at: datetime | None  # null while it's open
    hours: float  # how long it lasted, or has so far
    cause: str | None
    response: str | None
    responded_at: datetime | None
    updated_at: datetime


class MappingRead(BaseModel):
    id: uuid.UUID
    cuec_id: str
    customer_id: uuid.UUID | None
    control_ref: str  # your control's id in your inventory
    control_name: str | None
    owner: str | None
    link: str | None
    auto_accept: bool
    inventory_version: str
    created_at: datetime
    updated_at: datetime


class MappingWrite(BaseModel):
    control_ref: str = Field(..., min_length=1, max_length=100, description="Your control's id in your inventory (BANK-IAM-07)")
    control_name: str | None = Field(None, max_length=300)
    owner: str | None = Field(None, max_length=200)
    link: str | None = Field(None, max_length=1000, description="The control in your GRC tool (https://...)")
    auto_accept: bool = Field(False, description="Accept and sign OpenClear's proposed attestations for this CUEC at once")


class MappingsWrite(BaseModel):
    mappings: list[MappingWrite] = Field(..., max_length=50)


class MappingImport(BaseModel):
    added: int
    problems: list[str]


class AttestationFile(BaseModel):
    name: str
    sha256: str
    size: int


class AttestationRead(BaseModel):
    id: uuid.UUID
    cuec_id: str
    party: Literal["bank", "customer", "host"]
    customer_id: uuid.UUID | None
    installation: bool  # a host CUEC attested once for the installation by its platform staff
    period_kind: str
    period_start: date
    period_end: date  # inclusive
    result: Result
    method: Literal["automated", "manual"]
    status: Literal["prepared", "accepted", "rejected", "superseded"]
    statement: str | None
    evidence: dict[str, Any]
    files: list[AttestationFile]
    mappings: list[str]  # your controls mapped to its CUEC
    prepared_by: str  # a person's email, or OpenClear
    prepared_at: datetime
    accepted_by: str | None
    accepted_at: datetime | None
    auto_accepted: bool
    rejected_reason: str | None
    supersedes_id: uuid.UUID | None
    inventory_version: str
    sha256: str | None  # of the signed body, once accepted
    verified: bool | None  # the signature checks out with the installation's public key; null until accepted
    updated_at: datetime


class SignedAttestation(BaseModel):
    attestation: dict[str, Any]  # what the signature covers
    signature: dict[str, Any]  # algorithm, sha256, value, public_key


class AttestationDetail(AttestationRead):
    signed: SignedAttestation | None  # once accepted


class AttestationReject(BaseModel):
    reason: str = Field(..., min_length=1, max_length=2000)


class CustomerRollup(BaseModel):
    customer_id: uuid.UUID
    name: str
    cuecs: int  # the customer CUECs each customer attests
    attested: int  # of them, with an accepted attestation for a period overlapping the one asked about
    waiting: int  # prepared, not yet accepted
    last_accepted_at: datetime | None
    can_attest: int  # the customer's own people whose role can attest


class CuecEvidence(BaseModel):
    cuec_id: str
    evidence_level: Literal["automated", "assisted", "attested"]
    check: str | None
    period_start: date
    period_end: date
    proposed_result: Result | None  # an automated CUEC's result over the period by CT-D6's rule; null otherwise
    evidence: dict[str, Any]  # the numbers behind it (coverage, statuses, deviations, digests), or the related check's latest result
    attestations: list[AttestationRead]  # this CUEC's attestations for periods overlapping it


def _bank_only(ctx: TenantContext) -> None:
    if ctx.customer_id is not None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This is for the bank's own staff.")


def _period(start: date | None, end: date | None) -> None:
    if start and end and end < start:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "end is before start.")


def _between(column, start: date | None, end: date | None) -> list:
    out = []
    if start:
        out.append(column >= datetime.combine(start, time.min, tzinfo=timezone.utc))
    if end:
        out.append(column < datetime.combine(end + timedelta(days=1), time.min, tzinfo=timezone.utc))
    return out


def _page(db: Session, model, conditions: list, order: tuple, page: Page, since: Since | None) -> tuple[list, int]:
    total = db.execute(select(func.count()).select_from(model).where(*conditions)).scalar_one()
    stmt = select(model).where(*conditions).order_by(*order).limit(page.limit)
    rows = list(db.execute(stmt if since else stmt.offset(page.offset)).scalars())
    return rows, total


def _control(control_id: str) -> controls.OcControl:
    found = next((c for c in controls.CONTROLS if c.id == control_id), None)
    if found is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such control.")
    return found


def _visible_cuec(ctx: TenantContext, cuec_id: str) -> cuecs.Cuec:
    found = cuecs.by_id().get(cuec_id)
    if found is None or found.withdrawn or found.party not in (("customer",) if ctx.customer_id else ("bank", "host")):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such CUEC for you.")
    return found


def _unprocessable(exc: AttestationError) -> HTTPException:
    return HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc))


def _record(db: Session, ctx: TenantContext, action: str, summary: str, resource_type: str, resource_id) -> None:
    audit_log_service.record_action(db, ctx.tenant_id, actor_user_id=ctx.user_id, channel="api", action=action, summary=summary,
                                     resource_type=resource_type, resource_id=resource_id)


@router.get("/controls/{control_id}/results", response_model=list[TestResultRead])
def control_results(control_id: str, request: Request, response: Response,
                    start: date | None = Query(None, description="First day, YYYY-MM-DD (UTC)"),
                    end: date | None = Query(None, description="Last day, inclusive"),
                    page: Page = Depends(page_params), since: Since | None = Depends(updated_since_param),
                    db: Session = Depends(get_db), ctx: TenantContext = Depends(_read)) -> list:
    """Every recorded result of a control's checks (`OC-AC-05`), oldest first: each scheduled run,
    and a page view or a person's run where it changed the status. A result never changes once
    recorded, so `updated_since` here means recorded at or after."""
    from openclear.domain.control_result import ControlTestResult

    _bank_only(ctx)
    _period(start, end)
    control = _control(control_id)
    conditions = [ControlTestResult.tenant_id == ctx.tenant_id, ControlTestResult.check_key.in_(control.checks),
                  *_between(ControlTestResult.recorded_at, start, end)]
    extra, order = changed_since(ControlTestResult, since, (ControlTestResult.recorded_at, ControlTestResult.id), changed="recorded_at")
    rows, total = _page(db, ControlTestResult, conditions + extra.get("where", []), order, page, since)
    set_page_headers(request, response, page, total, since=since, rows=rows, changed="recorded_at")
    return rows


def _deviation(d) -> dict:
    opened = d.opened_at if d.opened_at.tzinfo else d.opened_at.replace(tzinfo=timezone.utc)
    closed = d.closed_at or datetime.now(timezone.utc)
    closed = closed if closed.tzinfo else closed.replace(tzinfo=timezone.utc)
    return {"id": d.id, "check_key": d.check_key, "status": d.status, "opened_at": d.opened_at, "closed_at": d.closed_at,
            "hours": round((closed - opened).total_seconds() / 3600, 1), "cause": d.cause, "response": d.response,
            "responded_at": d.responded_at, "updated_at": d.updated_at}


@router.get("/controls/{control_id}/deviations", response_model=list[DeviationRead])
def control_deviations(control_id: str, request: Request, response: Response,
                       start: date | None = Query(None, description="Deviations open on or after this day"),
                       end: date | None = Query(None, description="Deviations opened on or before this day"),
                       page: Page = Depends(page_params), since: Since | None = Depends(updated_since_param),
                       db: Session = Depends(get_db), ctx: TenantContext = Depends(_read)) -> list:
    """The deviations of a control's checks: each stretch of time a check was at FAIL (or WARN,
    where the bank says so), with the cause and response once recorded. Oldest first."""
    from openclear.domain.control_result import ControlDeviation

    _bank_only(ctx)
    _period(start, end)
    control = _control(control_id)
    conditions = [ControlDeviation.tenant_id == ctx.tenant_id, ControlDeviation.check_key.in_(control.checks)]
    if start:
        conditions.append(or_(ControlDeviation.closed_at.is_(None),
                              ControlDeviation.closed_at >= datetime.combine(start, time.min, tzinfo=timezone.utc)))
    if end:
        conditions.append(ControlDeviation.opened_at < datetime.combine(end + timedelta(days=1), time.min, tzinfo=timezone.utc))
    extra, order = changed_since(ControlDeviation, since, (ControlDeviation.opened_at, ControlDeviation.id))
    rows, total = _page(db, ControlDeviation, conditions + extra.get("where", []), order, page, since)
    set_page_headers(request, response, page, total, since=since, rows=rows)
    return [_deviation(d) for d in rows]


@router.get("/mappings", response_model=list[MappingRead])
def list_mappings(request: Request, response: Response, cuec_id: str | None = Query(None),
                  page: Page = Depends(page_params), since: Since | None = Depends(updated_since_param),
                  db: Session = Depends(get_db), ctx: TenantContext = Depends(_read)) -> list:
    """Your mappings: which of your own controls cover each CUEC (the bank's, or a customer's own)."""
    from openclear.domain.cuec_attestation import CuecMapping

    conditions = [CuecMapping.tenant_id == ctx.tenant_id,
                  CuecMapping.customer_id == ctx.customer_id if ctx.customer_id else CuecMapping.customer_id.is_(None)]
    if cuec_id:
        conditions.append(CuecMapping.cuec_id == cuec_id)
    extra, order = changed_since(CuecMapping, since, (CuecMapping.cuec_id, CuecMapping.control_ref, CuecMapping.id))
    rows, total = _page(db, CuecMapping, conditions + extra.get("where", []), order, page, since)
    set_page_headers(request, response, page, total, since=since, rows=rows)
    return rows


@router.post("/mappings/import", response_model=MappingImport)
async def import_mappings(file: UploadFile = File(..., description="cuecs.csv with your your_control_id (and your_control_owner, "
                                                                   "your_control_name, your_control_link) filled in"),
                          db: Session = Depends(get_db), ctx: TenantContext = Depends(_manage)) -> dict:
    """Load mappings from the CUEC inventory's CSV (docs/compliance/cuecs.csv) with your controls
    filled in. Rows left blank are skipped, and a mapping already there isn't added twice."""
    content = await file.read(FILE_LIMIT + 1)
    if len(content) > FILE_LIMIT:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "That file is too large.")
    try:
        added, problems = attestation_service.import_csv(db, ctx.tenant_id, ctx.customer_id, content, user_id=ctx.user_id)
    except AttestationError as exc:
        db.rollback()
        raise _unprocessable(exc) from None
    _record(db, ctx, "compliance.mapping.changed", f"Imported {added} CUEC mappings from {file.filename}", "tenant", ctx.tenant_id)
    db.commit()
    return {"added": added, "problems": problems}


@router.put("/cuecs/{cuec_id}/mappings", response_model=list[MappingRead])
def set_mappings(cuec_id: str, body: MappingsWrite, db: Session = Depends(get_db), ctx: TenantContext = Depends(_manage)) -> list:
    """Set a CUEC's mappings to exactly these, matched on `control_ref`: new ones are added,
    ones left out removed, the rest updated. Turning `auto_accept` on or off is recorded in the
    action log, and on is a security event."""
    _visible_cuec(ctx, cuec_id)
    try:
        rows, changes = attestation_service.set_mappings(db, ctx.tenant_id, ctx.customer_id, cuec_id, [m.model_dump() for m in body.mappings],
                                                         user_id=ctx.user_id)
    except AttestationError as exc:
        db.rollback()
        raise _unprocessable(exc) from None
    parts = [f"{label} {', '.join(changes[key])}" for key, label in (("added", "added"), ("removed", "removed"), ("updated", "updated"))
             if changes[key]]
    if parts:
        _record(db, ctx, "compliance.mapping.changed", f"Mappings of {cuec_id}: " + "; ".join(parts), "tenant", ctx.tenant_id)
    for mapping, on in changes["auto_accept"]:
        _record(db, ctx, "compliance.auto_accept.changed",
                f"{'Turned on' if on else 'Turned off'} accepting {cuec_id}'s proposed attestations automatically ({mapping.control_ref})",
                "cuec_mapping", mapping.id)
    db.commit()
    return rows


def _attestation_rows(db: Session, rows: list) -> list[dict]:
    """Attestations as the API gives them, with emails and mappings looked up once."""
    from openclear.domain.cuec_attestation import CuecMapping
    from openclear.domain.user import User

    ids = {a.prepared_by_user_id for a in rows} | {a.accepted_by_user_id for a in rows}
    emails = {row.id: row.email for row in db.execute(select(User.id, User.email).where(User.id.in_([i for i in ids if i]))).all()} if any(ids) else {}
    mapped: dict[tuple, list[str]] = {}
    tenants = {a.tenant_id for a in rows if a.tenant_id}
    if tenants:
        for m in db.execute(select(CuecMapping).where(CuecMapping.tenant_id.in_(tenants)).order_by(CuecMapping.control_ref)).scalars():
            mapped.setdefault((m.tenant_id, m.customer_id, m.cuec_id), []).append(m.control_ref)
    by_id = cuecs.by_id()
    out = []
    for a in rows:
        out.append({
            "id": a.id, "cuec_id": a.cuec_id, "party": by_id[a.cuec_id].party, "customer_id": a.customer_id, "installation": a.tenant_id is None,
            "period_kind": a.period_kind, "period_start": a.period_start, "period_end": a.period_end, "result": a.result, "method": a.method,
            "status": a.status, "statement": a.statement, "evidence": a.evidence or {},
            "files": [{"name": f["name"], "sha256": f["sha256"], "size": f["size"]} for f in a.files or []],
            "mappings": mapped.get((a.tenant_id, a.customer_id, a.cuec_id), []),
            "prepared_by": emails.get(a.prepared_by_user_id) or "OpenClear", "prepared_at": a.prepared_at,
            "accepted_by": emails.get(a.accepted_by_user_id) or ("OpenClear, by an auto-accepting mapping" if a.auto_accepted else None),
            "accepted_at": a.accepted_at, "auto_accepted": a.auto_accepted, "rejected_reason": a.rejected_reason,
            "supersedes_id": a.supersedes_id, "inventory_version": a.inventory_version, "sha256": a.sha256,
            "verified": attestation_service.verify(a) if a.signature else None, "updated_at": a.updated_at,
        })
    return out


def _attestation_conditions(db: Session, ctx: TenantContext, *, start: date | None, end: date | None, status_: str | None,
                            cuec_id: str | None, control: str | None, customer_id: uuid.UUID | None) -> list:
    """Which attestations a session reads: its own scope's (a bank also a customer's, by
    customer_id), and for a bank the installation's host attestations when its platform staff
    attest them."""
    from openclear.domain.cuec_attestation import CuecAttestation, CuecMapping

    if customer_id is not None:
        _bank_only(ctx)
    scope_customer = ctx.customer_id or customer_id
    own = (CuecAttestation.tenant_id == ctx.tenant_id) & (
        CuecAttestation.customer_id == scope_customer if scope_customer else CuecAttestation.customer_id.is_(None))
    if scope_customer is None and "host" not in attestation_service.parties_for(None):
        own = or_(own, CuecAttestation.tenant_id.is_(None))
    conditions = [own]
    if start:
        conditions.append(CuecAttestation.period_end >= start)
    if end:
        conditions.append(CuecAttestation.period_start <= end)
    if status_:
        conditions.append(CuecAttestation.status == status_)
    if cuec_id:
        conditions.append(CuecAttestation.cuec_id == cuec_id)
    if control:
        mapped = db.execute(select(CuecMapping.cuec_id).where(
            CuecMapping.tenant_id == ctx.tenant_id, CuecMapping.control_ref == control,
            CuecMapping.customer_id == scope_customer if scope_customer else CuecMapping.customer_id.is_(None))).scalars().all()
        conditions.append(CuecAttestation.cuec_id.in_(mapped))
    return conditions


@router.get("/cuecs/{cuec_id}/evidence", response_model=CuecEvidence)
def cuec_evidence(cuec_id: str, start: date = Query(..., description="First day, YYYY-MM-DD"), end: date = Query(..., description="Last day, inclusive"),
                  db: Session = Depends(get_db), ctx: TenantContext = Depends(_read)) -> dict:
    """The evidence behind a CUEC for a period. For the bank's automated CUECs, the result
    OpenClear would propose by CT-D6's rule (coverage of at least 95% of scheduled runs, no
    deviation longer than the grace period, every population test passed) and the numbers
    behind it: runs expected and recorded, statuses, deviations and the results' digests. For
    the rest, the related check's latest result. With the CUEC's attestations for the period."""
    from openclear.domain.cuec_attestation import CuecAttestation
    from openclear.services import control_results_service

    _period(start, end)
    cuec = _visible_cuec(ctx, cuec_id)
    proposed, evidence = None, {}
    if ctx.customer_id is None and cuec.evidence == "automated" and cuec.check:
        proposed, evidence = attestation_service.evaluate(db, ctx.tenant_id, cuec, start, end)
    elif cuec.check:
        latest = control_results_service.latest(db, ctx.tenant_id, cuec.check)
        evidence = {"related_check": cuec.check, "related_check_latest": None if latest is None else {
            "status": latest.status, "summary": latest.summary, "recorded_at": latest.recorded_at.isoformat(),
            "evidence_sha256": latest.evidence_sha256}}
    conditions = _attestation_conditions(db, ctx, start=start, end=end, status_=None, cuec_id=cuec_id, control=None, customer_id=None)
    rows = db.execute(select(CuecAttestation).where(*conditions).order_by(CuecAttestation.period_end, CuecAttestation.prepared_at)).scalars().all()
    return {"cuec_id": cuec.id, "evidence_level": cuec.evidence, "check": cuec.check, "period_start": start, "period_end": end,
            "proposed_result": proposed, "evidence": evidence, "attestations": _attestation_rows(db, list(rows))}


@router.post("/cuecs/{cuec_id}/attestations", response_model=AttestationRead, status_code=status.HTTP_201_CREATED)
async def record_attestation(cuec_id: str, start: date = Form(..., description="First day of the period"),
                             end: date = Form(..., description="Last day, inclusive; the period must have ended"),
                             result: ManualResult = Form(...), statement: str = Form(..., min_length=1, max_length=8000,
                                                                                    description="What you attest, and how you know"),
                             evidence_file: UploadFile | None = File(None, description="Evidence to keep with it, under 10 MB "
                                                                                       "(several: a zip)"),
                             db: Session = Depends(get_db), ctx: TenantContext = Depends(_attest)) -> dict:
    """Record a manual attestation of a CUEC for a period, with an evidence file if you have one
    (kept read-only, with its SHA-256). It's prepared, not yet signed: someone with
    `compliance:attest` accepts it, and under the bank's "second person" setting that's
    someone else."""
    _visible_cuec(ctx, cuec_id)
    uploads = []
    if evidence_file is not None and evidence_file.filename:
        data = await evidence_file.read(FILE_LIMIT + 1)
        if len(data) > FILE_LIMIT:
            raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "The file must be under 10 MB.")
        uploads.append((evidence_file.filename, data))
    try:
        files = attestation_service.store_files(ctx.tenant_id, uploads)
        attestation = attestation_service.record_manual(db, ctx.tenant_id, ctx.customer_id, cuec_id, start=start, end=end, result=result,
                                                        statement=statement, files=files, user_id=ctx.user_id)
    except AttestationError as exc:
        db.rollback()
        raise _unprocessable(exc) from None
    _record(db, ctx, "compliance.attestation.prepared", f"Prepared an attestation of {cuec_id} for {start} to {end}: {result}"
            + (f", with {len(files)} files" if files else ""), "cuec_attestation", attestation.id)
    db.commit()
    return _attestation_rows(db, [attestation])[0]


@router.get("/attestations", response_model=list[AttestationRead])
def list_attestations(request: Request, response: Response,
                      start: date | None = Query(None, description="Attestations for periods ending on or after this day"),
                      end: date | None = Query(None, description="Attestations for periods starting on or before this day"),
                      status_: Literal["prepared", "accepted", "rejected", "superseded"] | None = Query(None, alias="status"),
                      cuec_id: str | None = Query(None),
                      control: str | None = Query(None, description="Only the CUECs mapped to this control of yours (BANK-IAM-07)"),
                      customer_id: uuid.UUID | None = Query(None, description="The bank only: a customer's attestations instead of the bank's"),
                      page: Page = Depends(page_params), since: Since | None = Depends(updated_since_param),
                      db: Session = Depends(get_db), ctx: TenantContext = Depends(_read)) -> list:
    """Attestations, newest period first; with `updated_since`, those prepared, accepted,
    rejected or superseded since, oldest change first. Each accepted one has its `sha256`, and
    `verified` says its signature checks out; the signed body is at `GET /attestations/{id}`."""
    from openclear.domain.cuec_attestation import CuecAttestation

    _period(start, end)
    conditions = _attestation_conditions(db, ctx, start=start, end=end, status_=status_, cuec_id=cuec_id, control=control, customer_id=customer_id)
    extra, order = changed_since(CuecAttestation, since, (CuecAttestation.period_end.desc(), CuecAttestation.cuec_id, CuecAttestation.id))
    rows, total = _page(db, CuecAttestation, conditions + extra.get("where", []), order, page, since)
    set_page_headers(request, response, page, total, since=since, rows=rows)
    return _attestation_rows(db, rows)


_CSV_COLUMNS = ("id", "cuec_id", "party", "customer_id", "installation", "period_kind", "period_start", "period_end", "result", "method",
                "status", "mappings", "prepared_by", "prepared_at", "accepted_by", "accepted_at", "auto_accepted", "supersedes_id",
                "inventory_version", "sha256", "verified", "csf", "soc2", "statement")


@router.get("/attestations/export", **file_response("application/octet-stream", "The period's attestations: JSON, CSV or OSCAL"))
def export_attestations(start: date = Query(..., description="First day, YYYY-MM-DD"), end: date = Query(..., description="Last day, inclusive"),
                        format: Literal["json", "csv", "oscal"] = "json", db: Session = Depends(get_db), ctx: TenantContext = Depends(_read)) -> Response:
    """Every accepted (and superseded) attestation for periods overlapping [start, end]. JSON:
    each one signed, as `GET /attestations/{id}` gives it, with the public key to check them by.
    CSV: one row each. OSCAL: the bank's assessment results for the period (the bank only), with
    the attestations as OSCAL attestations."""
    from openclear.domain.cuec_attestation import CuecAttestation

    _period(start, end)
    if format == "oscal":
        _bank_only(ctx)
        body = oscal_service.assessment_results(db, ctx.tenant_id, start, end)
        return Response(json.dumps(body, indent=2), media_type="application/json",
                        headers={"Content-Disposition": f'attachment; filename="assessment-results-{start}-{end}.json"'})
    conditions = _attestation_conditions(db, ctx, start=start, end=end, status_=None, cuec_id=None, control=None, customer_id=None)
    rows = list(db.execute(select(CuecAttestation).where(*conditions, CuecAttestation.status.in_(("accepted", "superseded")))
                           .order_by(CuecAttestation.period_end, CuecAttestation.cuec_id)).scalars())
    name = f"attestations-{start}-{end}"
    if format == "json":
        from openclear.services.evidence_service import _public_key_pem

        body = {"format": "openclear-attestations/1", "period": {"start": start.isoformat(), "end": end.isoformat()},
                "public_key": _public_key_pem(),
                "attestations": [{**attestation_service.export(a), "status": a.status} for a in rows]}
        return Response(json.dumps(body, indent=2, sort_keys=True), media_type="application/json",
                        headers={"Content-Disposition": f'attachment; filename="{name}.json"'})
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=_CSV_COLUMNS, extrasaction="ignore", lineterminator="\n")
    writer.writeheader()
    for row in _attestation_rows(db, rows):
        cuec = cuecs.by_id()[row["cuec_id"]]
        soc2 = cuec.as_dict()["soc2"]
        writer.writerow({**row, "mappings": "; ".join(row["mappings"]), "csf": "; ".join(k["id"] for k in cuec.csf),
                         "soc2": "; ".join(soc2) if soc2 is not None else ""})
    return Response(out.getvalue(), media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="{name}.csv"'})


def _found(db: Session, ctx: TenantContext, attestation_id: uuid.UUID):
    found = attestation_service.get_attestation(db, ctx.tenant_id, ctx.customer_id, attestation_id)
    if found is None or (found.tenant_id is None and "host" in attestation_service.parties_for(None)) \
            or (found.tenant_id is None and ctx.customer_id is not None):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such attestation.")
    return found


@router.get("/attestations/{attestation_id}", response_model=AttestationDetail)
def get_attestation(attestation_id: uuid.UUID, db: Session = Depends(get_db), ctx: TenantContext = Depends(_read)) -> dict:
    """An attestation, with its signed body once accepted: `signed.attestation` is what the
    signature covers, `signed.signature` the ECDSA P-256 signature over the SHA-256 of its
    canonical JSON (sorted keys, no spaces) and the public key to check it with."""
    found = _found(db, ctx, attestation_id)
    row = _attestation_rows(db, [found])[0]
    return {**row, "signed": attestation_service.export(found) if found.signature else None}


@router.post("/attestations/{attestation_id}/accept", response_model=AttestationDetail)
def accept_attestation(attestation_id: uuid.UUID, db: Session = Depends(get_db), ctx: TenantContext = Depends(_attest)) -> dict:
    """Accept a prepared attestation: it's signed, and any earlier accepted one for the same CUEC
    and period is superseded. Under the bank's "second person" setting, whoever prepared a manual
    one can't accept it."""
    found = _found(db, ctx, attestation_id)
    if found.tenant_id is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such attestation.")
    try:
        attestation_service.accept(db, found, user_id=ctx.user_id)
    except AttestationError as exc:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None
    _record(db, ctx, "compliance.attestation.accepted", f"Accepted {found.cuec_id} for {found.period_start} to {found.period_end}: {found.result}",
            "cuec_attestation", found.id)
    attestation_service.announce_accepted(db, ctx.tenant_id, found)
    db.commit()
    return {**_attestation_rows(db, [found])[0], "signed": attestation_service.export(found)}


@router.post("/attestations/{attestation_id}/reject", response_model=AttestationDetail)
def reject_attestation(attestation_id: uuid.UUID, body: AttestationReject, db: Session = Depends(get_db),
                       ctx: TenantContext = Depends(_attest)) -> dict:
    """Reject a prepared attestation, saying why (a security event)."""
    found = _found(db, ctx, attestation_id)
    if found.tenant_id is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such attestation.")
    try:
        attestation_service.reject(db, found, reason=body.reason, user_id=ctx.user_id)
    except AttestationError as exc:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None
    _record(db, ctx, "compliance.attestation.rejected",
            f"Rejected {found.cuec_id} for {found.period_start} to {found.period_end}: {body.reason.strip()[:300]}", "cuec_attestation", found.id)
    db.commit()
    return {**_attestation_rows(db, [found])[0], "signed": None}


@router.get("/customers", response_model=list[CustomerRollup])
def customer_rollup(start: date | None = Query(None, description="First day (default: the start of last year)"),
                    end: date | None = Query(None, description="Last day, inclusive (default: the end of last year)"),
                    db: Session = Depends(get_db), ctx: TenantContext = Depends(_read)) -> list:
    """The bank's roll-up of its customers' own CUECs (CT-D13): for each active customer, how many
    it has attested for the period, how many wait to be accepted, and how many of its people can
    attest. Read a customer's attestations themselves with `GET /attestations?customer_id=`."""
    _bank_only(ctx)
    default_start, default_end = attestation_service.previous_year(date.today())
    start, end = start or default_start, end or default_end
    _period(start, end)
    return attestation_service.customer_rollup(db, ctx.tenant_id, start, end)


@router.get("/oscal/component-definition", response_model=dict[str, Any])
def oscal_component_definition(ctx: TenantContext = Depends(_read)) -> dict:
    """OpenClear as an OSCAL 1.2.1 component definition against NIST SP 800-53 Rev. 5: one
    implemented requirement per control, each OpenClear control on it as an `openclear-control`
    property and each CUEC as a `cuec` property (its party as the class) in the namespace
    `https://github.com/chaffed/OpenClear/ns/oscal`."""
    return oscal_service.component_definition()


@router.get("/oscal/assessment-results", response_model=dict[str, Any])
def oscal_assessment_results(start: date = Query(..., description="First day, YYYY-MM-DD"), end: date = Query(..., description="Last day, inclusive"),
                             db: Session = Depends(get_db), ctx: TenantContext = Depends(_read)) -> dict:
    """The bank's control testing for a period as OSCAL 1.2.1 assessment results: an observation
    per check that ran (its results' counts and digest), a finding per deviation, and each
    accepted CUEC attestation."""
    _bank_only(ctx)
    _period(start, end)
    return oscal_service.assessment_results(db, ctx.tenant_id, start, end)
