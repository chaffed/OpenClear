# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Settings → Controls (services/controls_service.py; COMPLIANCE_PLAN.md C4)."""

import uuid
from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy.orm import Session

from openclear.db.session import get_db
from openclear.db.tenancy import TenantContext
from openclear.services import controls_service
from openclear.web.deps import render_template, require_web_permission
from openclear.web.redirects import see_other
from openclear.web.security import verify_csrf
from openclear.web.errors import shown

router = APIRouter(tags=["web-controls"])


@router.get("/ui/admin/controls")
def controls_page(request: Request, db: Session = Depends(get_db),
                  ctx: TenantContext = Depends(require_web_permission("audit_log:read"))) -> HTMLResponse:
    from openclear.services import long_runs

    today = datetime.now(timezone.utc).date()  # export periods are UTC days
    export_run = None
    if export_id := request.query_params.get("export"):
        try:
            export_run = long_runs.get_run(db, ctx.tenant_id, uuid.UUID(export_id), long_runs.EVIDENCE)
        except ValueError:
            export_run = None
    from openclear.api.v1.compliance import inventory
    from openclear.compliance import controls as inventory_module

    live = controls_service.status(db, ctx.tenant_id)
    rows = inventory(live)
    families = [(label, [c for c in rows if c["family"] == key and not c["withdrawn"]]) for key, label in inventory_module.FAMILIES.items()]
    from openclear.services import control_results_service

    open_deviations = [d for d in control_results_service.deviations(db, ctx.tenant_id, limit=50) if d.closed_at is None]
    titles = {c.key: c.title for c in live}
    return render_template(request, "admin/controls.html", ctx=ctx, result=controls_service.summary(live), families=families,
                           open_deviations=open_deviations, check_titles=titles,
                           default_start=today.replace(day=1).isoformat(), default_end=today.isoformat(),
                           export_run=export_run, export_state=long_runs.state(export_run) if export_run else None)


@router.get("/ui/admin/controls/summary")
def controls_summary(db: Session = Depends(get_db), ctx: TenantContext = Depends(require_web_permission("audit_log:read"))):
    """The Controls tile's state on the control panel: "All pass", "1 failing", "2 warnings",
    in words like every other tile's (UI_PLAN.md F17)."""
    from fastapi.responses import JSONResponse

    counts = controls_service.summary(controls_service.status(db, ctx.tenant_id))["counts"]
    if counts.get("FAIL"):
        return JSONResponse({"state": f"{counts['FAIL']} failing", "tone": "danger"})
    if counts.get("WARN"):
        return JSONResponse({"state": f"{counts['WARN']} warning" + ("" if counts["WARN"] == 1 else "s"), "tone": "warn"})
    return JSONResponse({"state": "All pass", "tone": "ok"})


@router.post("/ui/admin/controls/evidence")
def export_evidence(start: date = Form(...), end: date = Form(...), format: str = Form("pdf"), db: Session = Depends(get_db),
                    ctx: TenantContext = Depends(require_web_permission("audit_log:read")), _csrf: None = Depends(verify_csrf)) -> Response:

    from openclear.api.v1.audit import export_response

    from openclear.services import evidence_service, long_runs
    from openclear.workers.jobs import jobs_queued

    fmt = "json" if format == "json" else "pdf"
    try:
        if long_runs.evidence_runs_inline(start, end):
            return export_response(db, ctx, start, end, fmt, "web")
        run = long_runs.start_evidence_export(db, ctx.tenant_id, start=start, end=end, fmt=fmt, user_id=ctx.user_id, channel="web")
    except HTTPException as exc:
        return see_other("/ui/admin/controls", error=str(exc.detail))
    except evidence_service.EvidenceError as exc:
        return see_other("/ui/admin/controls", error=shown(exc))
    db.commit()
    jobs_queued(db.get_bind())
    return see_other("/ui/admin/controls", export=run.id)


@router.get("/ui/admin/controls/evidence/{run_id}")
def download_evidence(run_id: uuid.UUID, db: Session = Depends(get_db),
                      ctx: TenantContext = Depends(require_web_permission("audit_log:read"))) -> Response:
    from openclear.services import long_runs
    from openclear.web.deps import WebNotFound

    run = long_runs.get_run(db, ctx.tenant_id, run_id, long_runs.EVIDENCE)
    if run is None or long_runs.state(run) != "done":
        raise WebNotFound()
    body, headers = long_runs.evidence_file(run)
    return Response(body, media_type="application/pdf" if run.report["format"] == "pdf" else "application/json", headers=headers)
