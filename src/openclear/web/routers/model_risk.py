# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Admin → Model risk (services/mrm_service.py; COMPLIANCE_PLAN.md C2): replay a past file
through today's rules, run the anomaly-capture test, and read the reports."""

import uuid
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from openclear.api.v1.mrm import MAX_REPLAY_BYTES
from openclear.db.session import get_db
from openclear.db.tenancy import TenantContext
from openclear.domain.control_run import ControlRun
from openclear.services import mrm_service
from openclear.web.deps import render_template, require_web_permission
from openclear.web.security import verify_csrf

router = APIRouter(tags=["web-model-risk"])
_manage = require_web_permission("admin:manage")


@router.get("/ui/admin/model-risk")
def model_risk_page(request: Request, run: uuid.UUID | None = None, db: Session = Depends(get_db),
                    ctx: TenantContext = Depends(_manage)) -> HTMLResponse:
    shown = db.get(ControlRun, run) if run else None
    if shown is not None and shown.tenant_id != ctx.tenant_id:
        shown = None
    from openclear.services import long_runs

    runs = [r for r in mrm_service.recent_runs(db, ctx.tenant_id, limit=40) if r.kind in (mrm_service.REPLAY, mrm_service.ANOMALY_CAPTURE)][:20]
    return render_template(request, "admin/model_risk.html", ctx=ctx, runs=runs, shown=shown,
                           shown_state=long_runs.state(shown) if shown else None,
                           latest_capture=mrm_service.latest(db, ctx.tenant_id, mrm_service.ANOMALY_CAPTURE))


@router.post("/ui/admin/model-risk/replay")
def replay(file: UploadFile = File(...), db: Session = Depends(get_db), ctx: TenantContext = Depends(_manage),
           _csrf: None = Depends(verify_csrf)) -> RedirectResponse:
    content = file.file.read(MAX_REPLAY_BYTES + 1)
    if len(content) > MAX_REPLAY_BYTES:
        return RedirectResponse(f"/ui/admin/model-risk?error={quote('That file is too large to replay.')}", status_code=303)
    from openclear.services import long_runs
    from openclear.workers.jobs import jobs_queued

    try:
        run = long_runs.start_replay(db, ctx.tenant_id, filename=file.filename or "replay", content=content, user_id=ctx.user_id,
                                     channel="web")
    except mrm_service.ReplayError as exc:
        db.rollback()
        return RedirectResponse(f"/ui/admin/model-risk?error={quote(str(exc))}", status_code=303)
    db.commit()
    jobs_queued(db.get_bind())
    return RedirectResponse(f"/ui/admin/model-risk?run={run.id}", status_code=303)


@router.post("/ui/admin/model-risk/anomaly-capture")
def anomaly_capture(db: Session = Depends(get_db), ctx: TenantContext = Depends(_manage),
                    _csrf: None = Depends(verify_csrf)) -> RedirectResponse:
    run = mrm_service.anomaly_capture(db, ctx.tenant_id, user_id=ctx.user_id, channel="web")
    db.commit()
    return RedirectResponse(f"/ui/admin/model-risk?run={run.id}", status_code=303)
