# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Settings → Check return reasons: the list reviewers choose from when returning a check
(services/check_return_reason_service.py; UX_PLAN.md U4). Never deleted, only retired,
since past decisions name them. The same permission as ACH return reasons."""

import uuid

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from openclear.db.session import get_db
from openclear.db.tenancy import TenantContext
from openclear.services import audit_log_service, check_return_reason_service
from openclear.services.check_return_reason_service import InvalidCheckReturnReasonInput
from openclear.web.deps import WebNotFound, render_template, require_web_permission
from openclear.web.redirects import see_other
from openclear.web.security import verify_csrf

router = APIRouter(prefix="/ui/check-return-reasons", tags=["web-check-return-reasons"])
_PERMISSION = "ach_return_reason:manage"


def _record(db: Session, ctx: TenantContext, action: str, summary: str, reason) -> None:
    audit_log_service.record_action(db, ctx.tenant_id, actor_user_id=ctx.user_id, channel="web", action=f"check_return_reason.{action}",
                                    summary=summary, resource_type="check_return_reason", resource_id=reason.id)


@router.get("")
def list_reasons(request: Request, db: Session = Depends(get_db),
                 ctx: TenantContext = Depends(require_web_permission(_PERMISSION))) -> HTMLResponse:
    reasons = check_return_reason_service.list_check_return_reasons(db, ctx.tenant_id, active_only=False)
    return render_template(request, "check_return_reasons/list.html", ctx=ctx, reasons=reasons)


@router.get("/new")
def new_reason(request: Request, ctx: TenantContext = Depends(require_web_permission(_PERMISSION))) -> HTMLResponse:
    return render_template(request, "check_return_reasons/form.html", ctx=ctx, reason=None)


@router.post("")
def create_reason(request: Request, reason_text: str = Form(...), return_code: str = Form(""), db: Session = Depends(get_db),
                  ctx: TenantContext = Depends(require_web_permission(_PERMISSION)), _csrf: None = Depends(verify_csrf)) -> HTMLResponse:
    try:
        reason = check_return_reason_service.create_check_return_reason(db, ctx.tenant_id, reason_text=reason_text, return_code=return_code)
    except InvalidCheckReturnReasonInput as exc:
        db.rollback()
        return render_template(request, "check_return_reasons/form.html", ctx=ctx, reason=None, reason_text=reason_text,
                               return_code=return_code, error=str(exc), status_code=422)
    _record(db, ctx, "create", f"Added check return reason {reason.reason_text!r}", reason)
    db.commit()
    return see_other("/ui/check-return-reasons", flash="Return reason added.")


@router.get("/{reason_id}/edit")
def edit_reason(request: Request, reason_id: uuid.UUID, db: Session = Depends(get_db),
                ctx: TenantContext = Depends(require_web_permission(_PERMISSION))) -> HTMLResponse:
    reason = check_return_reason_service.get_check_return_reason(db, ctx.tenant_id, reason_id)
    if reason is None:
        raise WebNotFound()
    return render_template(request, "check_return_reasons/form.html", ctx=ctx, reason=reason)


@router.post("/{reason_id}")
def update_reason(request: Request, reason_id: uuid.UUID, reason_text: str = Form(...), return_code: str = Form(""),
                  db: Session = Depends(get_db), ctx: TenantContext = Depends(require_web_permission(_PERMISSION)),
                  _csrf: None = Depends(verify_csrf)) -> HTMLResponse:
    try:
        reason = check_return_reason_service.update_check_return_reason(db, ctx.tenant_id, reason_id, reason_text=reason_text,
                                                                         return_code=return_code)
    except InvalidCheckReturnReasonInput as exc:
        db.rollback()
        existing = check_return_reason_service.get_check_return_reason(db, ctx.tenant_id, reason_id)
        if existing is None:
            raise WebNotFound() from None
        return render_template(request, "check_return_reasons/form.html", ctx=ctx, reason=existing, reason_text=reason_text,
                               return_code=return_code, error=str(exc), status_code=422)
    if reason is None:
        raise WebNotFound()
    _record(db, ctx, "update", f"Updated check return reason {reason.reason_text!r}", reason)
    db.commit()
    return see_other("/ui/check-return-reasons", flash="Return reason updated.")


@router.post("/{reason_id}/{verdict}")
def set_active(reason_id: uuid.UUID, verdict: str, db: Session = Depends(get_db),
               ctx: TenantContext = Depends(require_web_permission(_PERMISSION)), _csrf: None = Depends(verify_csrf)) -> RedirectResponse:
    if verdict not in ("deactivate", "reactivate"):
        raise WebNotFound()
    reason = check_return_reason_service.set_active(db, ctx.tenant_id, reason_id, verdict == "reactivate")
    if reason is None:
        return see_other("/ui/check-return-reasons", error="Return reason not found.")
    _record(db, ctx, verdict, f"{verdict.capitalize()}d check return reason {reason.reason_text!r}", reason)
    db.commit()
    return see_other("/ui/check-return-reasons", flash=f"Return reason {verdict}d.")
