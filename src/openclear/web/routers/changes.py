# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Admin → Changes awaiting approval (services/change_request_service.py), and the
segregation-of-duties setting (auth/sod.py)."""

import uuid
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from openclear.auth import sod
from openclear.db.session import get_db
from openclear.db.tenancy import TenantContext
from openclear.services import audit_log_service, change_request_service, security_group_service
from openclear.web.deps import render_template, require_web_permission
from openclear.web.security import verify_csrf

router = APIRouter(tags=["web-changes"])


@router.get("/ui/admin/changes")
def changes_page(request: Request, db: Session = Depends(get_db),
                 ctx: TenantContext = Depends(require_web_permission("admin:manage"))) -> HTMLResponse:
    return render_template(request, "admin/changes.html", ctx=ctx, changes=change_request_service.pending(db, ctx.tenant_id))


@router.post("/ui/admin/changes/{change_id}/{verdict}")
def decide_change(change_id: uuid.UUID, verdict: str, db: Session = Depends(get_db),
                  ctx: TenantContext = Depends(require_web_permission("admin:manage")), _csrf: None = Depends(verify_csrf)) -> RedirectResponse:
    if verdict not in ("approve", "reject"):
        return RedirectResponse("/ui/admin/changes", status_code=303)
    try:
        change_request_service.decide(db, ctx.tenant_id, change_id, approve=verdict == "approve", user_id=ctx.user_id)
    except (change_request_service.ChangeRequestError, ValueError) as exc:
        db.rollback()
        return RedirectResponse(f"/ui/admin/changes?error={quote(str(exc))}", status_code=303)
    db.commit()
    return RedirectResponse(f"/ui/admin/changes?flash={'Approved' if verdict == 'approve' else 'Rejected'}.", status_code=303)


@router.get("/ui/settings/segregation-of-duties")
def sod_page(request: Request, db: Session = Depends(get_db),
             ctx: TenantContext = Depends(require_web_permission("tenant:manage"))) -> HTMLResponse:
    groups = security_group_service.list_security_groups(db, ctx.tenant_id)
    conflicts = [(g, sod.group_conflicts(g.permissions)) for g in groups]
    return render_template(request, "settings/sod.html", ctx=ctx, mode=sod.mode(db, ctx.tenant_id), rules=sod.RULES,
                           conflicts=[(g, c) for g, c in conflicts if c])


@router.post("/ui/settings/segregation-of-duties")
def set_sod_mode(mode: str = Form(...), db: Session = Depends(get_db),
                 ctx: TenantContext = Depends(require_web_permission("tenant:manage")), _csrf: None = Depends(verify_csrf)) -> RedirectResponse:
    try:
        sod.set_mode(db, ctx.tenant_id, mode)
    except ValueError as exc:
        db.rollback()
        return RedirectResponse(f"/ui/settings/segregation-of-duties?error={quote(str(exc))}", status_code=303)
    audit_log_service.record_action(db, ctx.tenant_id, actor_user_id=ctx.user_id, channel="web", action="tenant.update_sod_mode",
                                    summary=f"Segregation of duties set to {mode}", resource_type="tenant", resource_id=ctx.tenant_id)
    db.commit()
    return RedirectResponse("/ui/settings/segregation-of-duties?flash=Saved.", status_code=303)
