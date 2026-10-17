# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Settings → Changes awaiting approval (services/change_request_service.py), and the
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
from openclear.web.deps import render_template, require_any_web_permission, require_web_permission
from openclear.web.security import verify_csrf

router = APIRouter(tags=["web-changes"])


# Anyone who could approve some kind of change, or who asked for one, sees the queue.
_SEES_CHANGES = ("admin:manage", "tenant:manage", "security_group:manage", "user:manage", "api_client:manage", "customer:manage",
                 "compliance:manage", "compliance:attest")


@router.get("/ui/admin/changes")
def changes_page(request: Request, db: Session = Depends(get_db),
                 ctx: TenantContext = Depends(require_any_web_permission(*_SEES_CHANGES))) -> HTMLResponse:
    from openclear.domain.change_request import ChangeRequest
    from openclear.domain.user import User
    from sqlalchemy import select

    waiting = change_request_service.pending(db, ctx.tenant_id, customer_id=ctx.customer_id, all_customers=ctx.customer_id is None)
    recent = list(db.execute(select(ChangeRequest).where(ChangeRequest.tenant_id == ctx.tenant_id, ChangeRequest.status != "pending",
                                                         *( [ChangeRequest.customer_id == ctx.customer_id] if ctx.customer_id else []))
                             .order_by(ChangeRequest.decided_at.desc()).limit(20)).scalars())
    people = {u.id: u.email for u in db.execute(select(User).where(User.id.in_(
        {c.requested_by_user_id for c in waiting + recent} | {c.decided_by_user_id for c in recent}))).scalars()}
    rows = [{"change": c, "mine": c.requested_by_user_id == ctx.user_id, "can_decide": change_request_service.can_decide(db, c, ctx.user_id),
             "kind": change_request_service.change_kinds.get(c.kind)} for c in waiting]
    return render_template(request, "admin/changes.html", ctx=ctx, rows=rows, recent=recent, people=people)


@router.post("/ui/admin/changes/{change_id}/{verdict}")
def decide_change(change_id: uuid.UUID, verdict: str, reason: str = Form(""), db: Session = Depends(get_db),
                  ctx: TenantContext = Depends(require_any_web_permission(*_SEES_CHANGES)),
                  _csrf: None = Depends(verify_csrf)) -> RedirectResponse:
    if verdict not in ("approve", "reject", "withdraw"):
        return RedirectResponse("/ui/admin/changes", status_code=303)
    try:
        if verdict == "withdraw":
            change_request_service.withdraw(db, ctx.tenant_id, change_id, user_id=ctx.user_id)
        else:
            change_request_service.decide(db, ctx.tenant_id, change_id, approve=verdict == "approve", user_id=ctx.user_id, reason=reason)
    except (change_request_service.ChangeRequestError, ValueError) as exc:
        db.rollback()
        return RedirectResponse(f"/ui/admin/changes?error={quote(str(exc))}", status_code=303)
    db.commit()
    done = {"approve": "Approved: it has taken effect.", "reject": "Rejected.", "withdraw": "Withdrawn."}[verdict]
    return RedirectResponse(f"/ui/admin/changes?flash={quote(done)}", status_code=303)


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
    """Enforce or warn. Going back to warn needs a second admin (DUAL_CONTROL_PLAN.md)."""
    from openclear.web.routers.tenant_settings import waiting_message

    if mode not in ("warn", "enforce"):
        return RedirectResponse(f"/ui/settings/segregation-of-duties?error={quote('Mode is warn or enforce.')}", status_code=303)
    try:
        outcome = change_request_service.request_or_apply(db, "sod_mode", {"values": {"sod_mode": mode}}, tenant_id=ctx.tenant_id,
                                                          user_id=ctx.user_id)
    except ValueError as exc:
        db.rollback()
        return RedirectResponse(f"/ui/settings/segregation-of-duties?error={quote(str(exc))}", status_code=303)
    if outcome.waiting:
        db.commit()
        return RedirectResponse(f"/ui/settings/segregation-of-duties?flash={quote(waiting_message(outcome))}", status_code=303)
    if outcome.summary != "Nothing changed.":
        audit_log_service.record_action(db, ctx.tenant_id, actor_user_id=ctx.user_id, channel="web", action="tenant.update_sod_mode",
                                        summary=f"Segregation of duties set to {mode}", resource_type="tenant", resource_id=ctx.tenant_id)
    db.commit()
    return RedirectResponse("/ui/settings/segregation-of-duties?flash=Saved.", status_code=303)
