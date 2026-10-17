# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""CUECs, their mappings and attestations (CONTROLS_PLAN.md CT4; services/attestation_service.py).
The bank sees its own and the host's CUECs; a customer's own session (compliance:read) sees
its own, as "Your responsibilities". Reading needs compliance:read; mappings and settings
compliance:manage; preparing, accepting and rejecting attestations compliance:attest."""

import json
import uuid
from datetime import date
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.orm import Session

from openclear.compliance import cuecs
from openclear.db.session import get_db
from openclear.db.tenancy import TenantContext
from openclear.services import attestation_service, audit_log_service
from openclear.services.attestation_service import AttestationError
from openclear.web.deps import WebNotFound, render_template, require_web_permission
from openclear.web.security import verify_csrf

router = APIRouter(prefix="/ui/compliance", tags=["web-cuecs"])
_read = require_web_permission("compliance:read")
_manage = require_web_permission("compliance:manage")
_attest = require_web_permission("compliance:attest")
FILE_LIMIT = 10 * 1024 * 1024
BASE = "/ui/compliance/cuecs"


def _back(path: str, message: str) -> RedirectResponse:
    return RedirectResponse(f"{path}?flash={quote(message)}", status_code=303)


def _record(db: Session, ctx: TenantContext, action: str, summary: str, resource_type: str, resource_id) -> None:
    audit_log_service.record_action(db, ctx.tenant_id, actor_user_id=ctx.user_id, channel="web", action=action, summary=summary,
                                     resource_type=resource_type, resource_id=resource_id)


def _visible(ctx: TenantContext) -> list[cuecs.Cuec]:
    parties = ("customer",) if ctx.customer_id else ("bank", "host")
    return [c for c in cuecs.CUECS if c.party in parties and not c.withdrawn]


# Literal paths before /cuecs/{cuec_id} and /attestations/{attestation_id}.


@router.get("/cuecs")
def overview(request: Request, db: Session = Depends(get_db), ctx: TenantContext = Depends(_read)) -> HTMLResponse:
    from openclear.domain.tenant import Tenant

    mapped: dict[str, list] = {}
    for m in attestation_service.mappings(db, ctx.tenant_id, ctx.customer_id):
        mapped.setdefault(m.cuec_id, []).append(m)
    latest: dict[str, object] = {}
    waiting: dict[str, int] = {}
    for a in attestation_service.attestations(db, ctx.tenant_id, ctx.customer_id):
        if a.status == "accepted" and a.cuec_id not in latest:
            latest[a.cuec_id] = a
        if a.status == "prepared":
            waiting[a.cuec_id] = waiting.get(a.cuec_id, 0) + 1
    if ctx.customer_id is None and "host" not in attestation_service.parties_for(None):
        for a in attestation_service.installation_attestations(db):
            if a.status == "accepted" and a.cuec_id not in latest:
                latest[a.cuec_id] = a
    tenant = db.get(Tenant, ctx.tenant_id)
    groups = [(cuecs.PARTIES[p], [c for c in _visible(ctx) if c.party == p]) for p in (("customer",) if ctx.customer_id else ("bank", "host"))]
    return render_template(request, "compliance/cuecs.html", ctx=ctx, groups=groups, mapped=mapped, latest=latest, waiting=waiting,
                           tenant=tenant, periods=attestation_service.PERIODS, host_by_platform="host" not in attestation_service.parties_for(None))


@router.post("/settings")
def settings(attestation_period: str = Form("quarterly"), attest_second_person: bool = Form(False), db: Session = Depends(get_db),
             ctx: TenantContext = Depends(_manage), _csrf: None = Depends(verify_csrf)) -> RedirectResponse:
    from openclear.domain.tenant import Tenant

    if ctx.customer_id is not None or attestation_period not in attestation_service.PERIODS:
        return _back(BASE, "Choose monthly, quarterly or yearly.")
    tenant = db.get(Tenant, ctx.tenant_id)
    tenant.attestation_period, tenant.attest_second_person = attestation_period, attest_second_person
    _record(db, ctx, "compliance.settings.changed", f"Attestations {attestation_period}; second person {'required' if attest_second_person else 'not required'}",
            "tenant", ctx.tenant_id)
    db.commit()
    return _back(BASE, "Attestation settings saved.")


@router.post("/mappings/import")
async def import_mappings(upload_file: UploadFile, db: Session = Depends(get_db), ctx: TenantContext = Depends(_manage),
                          _csrf: None = Depends(verify_csrf)) -> RedirectResponse:
    content = await upload_file.read(FILE_LIMIT + 1)
    if len(content) > FILE_LIMIT:
        return _back(BASE, "That file is too large.")
    try:
        added, problems = attestation_service.import_csv(db, ctx.tenant_id, ctx.customer_id, content, user_id=ctx.user_id)
    except AttestationError as exc:
        db.rollback()
        return _back(BASE, str(exc))
    _record(db, ctx, "compliance.mapping.changed", f"Imported {added} CUEC mappings from {upload_file.filename}", "tenant", ctx.tenant_id)
    db.commit()
    return _back(BASE, f"Added {added} mappings." + (f" Skipped: {'; '.join(problems[:5])}" if problems else ""))


@router.post("/prepare")
def prepare_now(db: Session = Depends(get_db), ctx: TenantContext = Depends(_attest), _csrf: None = Depends(verify_csrf)) -> RedirectResponse:
    """Propose the last finished period's automated attestations now, rather than waiting for the nightly job."""
    from openclear.domain.tenant import Tenant

    if ctx.customer_id is not None:
        raise WebNotFound()
    tenant = db.get(Tenant, ctx.tenant_id)
    start, end = attestation_service.previous_period(tenant.attestation_period, date.today())
    made = attestation_service.prepare_automated(db, ctx.tenant_id, tenant.attestation_period, start, end)
    attestation_service.announce(db, ctx.tenant_id, made, channel="web", user_id=ctx.user_id)
    _record(db, ctx, "compliance.attestation.prepared", f"Prepared {len(made)} attestations for {start} to {end}", "tenant", ctx.tenant_id)
    db.commit()
    return _back(BASE, f"Prepared {len(made)} attestations for {start} to {end}.")


@router.post("/mappings/{mapping_id}/delete")
def delete_mapping(mapping_id: uuid.UUID, db: Session = Depends(get_db), ctx: TenantContext = Depends(_manage),
                   _csrf: None = Depends(verify_csrf)) -> RedirectResponse:
    mapping = attestation_service.get_mapping(db, ctx.tenant_id, ctx.customer_id, mapping_id)
    if mapping is None:
        raise WebNotFound()
    cuec_id = mapping.cuec_id
    db.delete(mapping)
    _record(db, ctx, "compliance.mapping.changed", f"Removed the mapping of {cuec_id} to {mapping.control_ref}", "tenant", ctx.tenant_id)
    db.commit()
    return _back(f"{BASE}/{cuec_id}", "Mapping removed.")


@router.post("/mappings/{mapping_id}/auto-accept")
def auto_accept(mapping_id: uuid.UUID, on: bool = Form(False), db: Session = Depends(get_db), ctx: TenantContext = Depends(_manage),
                _csrf: None = Depends(verify_csrf)) -> RedirectResponse:
    mapping = attestation_service.get_mapping(db, ctx.tenant_id, ctx.customer_id, mapping_id)
    if mapping is None:
        raise WebNotFound()
    mapping.auto_accept = on
    _record(db, ctx, "compliance.auto_accept.changed",
            f"{'Turned on' if on else 'Turned off'} accepting {mapping.cuec_id}'s proposed attestations automatically ({mapping.control_ref})",
            "cuec_mapping", mapping.id)
    db.commit()
    return _back(f"{BASE}/{mapping.cuec_id}", "Saved.")


@router.get("/attestations/{attestation_id}.json")
def download(attestation_id: uuid.UUID, db: Session = Depends(get_db), ctx: TenantContext = Depends(_read)) -> Response:
    found = attestation_service.get_attestation(db, ctx.tenant_id, ctx.customer_id, attestation_id)
    if found is None or found.status not in ("accepted", "superseded"):
        raise WebNotFound()
    body = json.dumps(attestation_service.export(found), indent=2, sort_keys=True)
    return Response(body, media_type="application/json",
                    headers={"Content-Disposition": f'attachment; filename="attestation-{found.cuec_id}-{found.period_end}.json"'})


@router.post("/attestations/{attestation_id}/accept")
def accept(attestation_id: uuid.UUID, db: Session = Depends(get_db), ctx: TenantContext = Depends(_attest),
           _csrf: None = Depends(verify_csrf)) -> RedirectResponse:
    found = attestation_service.get_attestation(db, ctx.tenant_id, ctx.customer_id, attestation_id)
    if found is None or found.tenant_id is None:
        raise WebNotFound()
    try:
        attestation_service.accept(db, found, user_id=ctx.user_id)
    except AttestationError as exc:
        db.rollback()
        return _back(f"{BASE}/{found.cuec_id}", str(exc))
    _record(db, ctx, "compliance.attestation.accepted", f"Accepted {found.cuec_id} for {found.period_start} to {found.period_end}: {found.result}",
            "cuec_attestation", found.id)
    attestation_service.announce_accepted(db, ctx.tenant_id, found)
    db.commit()
    return _back(f"{BASE}/{found.cuec_id}", "Accepted and signed.")


@router.post("/attestations/{attestation_id}/reject")
def reject(attestation_id: uuid.UUID, reason: str = Form(""), db: Session = Depends(get_db), ctx: TenantContext = Depends(_attest),
           _csrf: None = Depends(verify_csrf)) -> RedirectResponse:
    found = attestation_service.get_attestation(db, ctx.tenant_id, ctx.customer_id, attestation_id)
    if found is None or found.tenant_id is None:
        raise WebNotFound()
    try:
        attestation_service.reject(db, found, reason=reason, user_id=ctx.user_id)
    except AttestationError as exc:
        db.rollback()
        return _back(f"{BASE}/{found.cuec_id}", str(exc))
    _record(db, ctx, "compliance.attestation.rejected", f"Rejected {found.cuec_id} for {found.period_start} to {found.period_end}: {reason.strip()[:300]}",
            "cuec_attestation", found.id)
    db.commit()
    return _back(f"{BASE}/{found.cuec_id}", "Rejected.")


@router.get("/cuecs/{cuec_id}")
def cuec_page(request: Request, cuec_id: str, db: Session = Depends(get_db), ctx: TenantContext = Depends(_read)) -> HTMLResponse:
    from openclear.compliance import controls
    from openclear.domain.tenant import Tenant

    cuec = next((c for c in _visible(ctx) if c.id == cuec_id), None)
    if cuec is None:
        raise WebNotFound()
    rows = attestation_service.attestations(db, ctx.tenant_id, ctx.customer_id, cuec_id=cuec_id)
    installation = attestation_service.installation_attestations(db, cuec_id=cuec_id) if cuec.party == "host" else []
    start, end = attestation_service.previous_period(db.get(Tenant, ctx.tenant_id).attestation_period, date.today())
    return render_template(request, "compliance/cuec.html", ctx=ctx, cuec=cuec, rows=rows, installation=installation,
                           mappings=attestation_service.mappings(db, ctx.tenant_id, ctx.customer_id, cuec_id),
                           relied_on=[c for c in controls.CONTROLS if cuec_id in c.cuecs], results=attestation_service.MANUAL_RESULTS,
                           default_start=start, default_end=end, by_platform=cuec.party == "host" and "host" not in attestation_service.parties_for(None),
                           verified={a.id: attestation_service.verify(a) for a in rows + installation if a.signature})


@router.post("/cuecs/{cuec_id}/mappings")
def add_mapping(cuec_id: str, control_ref: str = Form(""), control_name: str = Form(""), owner: str = Form(""), link: str = Form(""),
                db: Session = Depends(get_db), ctx: TenantContext = Depends(_manage), _csrf: None = Depends(verify_csrf)) -> RedirectResponse:
    try:
        mapping = attestation_service.add_mapping(db, ctx.tenant_id, ctx.customer_id, cuec_id, control_ref=control_ref, control_name=control_name,
                                                  owner=owner, link=link, user_id=ctx.user_id)
    except AttestationError as exc:
        db.rollback()
        return _back(f"{BASE}/{cuec_id}", str(exc))
    _record(db, ctx, "compliance.mapping.changed", f"Mapped {cuec_id} to {mapping.control_ref}", "cuec_mapping", mapping.id)
    db.commit()
    return _back(f"{BASE}/{cuec_id}", "Mapping added.")


@router.post("/cuecs/{cuec_id}/attestations")
async def attest(request: Request, cuec_id: str, db: Session = Depends(get_db), ctx: TenantContext = Depends(_attest),
                 _csrf: None = Depends(verify_csrf)) -> RedirectResponse:
    form = await request.form()
    uploads = []
    for upload in form.getlist("evidence_file"):
        if not getattr(upload, "filename", ""):
            continue
        data = await upload.read(FILE_LIMIT + 1)
        if len(data) > FILE_LIMIT:
            return _back(f"{BASE}/{cuec_id}", "Each file must be under 10 MB.")
        uploads.append((upload.filename, data))
    files = attestation_service.store_files(ctx.tenant_id, uploads)
    try:
        attestation = attestation_service.record_manual(
            db, ctx.tenant_id, ctx.customer_id, cuec_id, start=date.fromisoformat(str(form.get("start"))),
            end=date.fromisoformat(str(form.get("end"))), result=str(form.get("result") or ""), statement=str(form.get("statement") or ""),
            files=files, user_id=ctx.user_id)
    except (AttestationError, ValueError) as exc:
        db.rollback()
        return _back(f"{BASE}/{cuec_id}", str(exc) if isinstance(exc, AttestationError) else "Choose the period's dates.")
    _record(db, ctx, "compliance.attestation.prepared", f"Prepared an attestation of {cuec_id} for {attestation.period_start} to "
            f"{attestation.period_end}: {attestation.result}" + (f", with {len(files)} files" if files else ""), "cuec_attestation", attestation.id)
    db.commit()
    return _back(f"{BASE}/{cuec_id}", "Prepared. It's signed once accepted.")
