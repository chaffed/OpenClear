# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import uuid

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from openclear.db.session import get_db
from openclear.db.tenancy import TenantContext
from openclear.domain.ach_return_reason import AchReturnReason
from openclear.repositories.ach_return_reason_repo import AchReturnReasonRepository
from openclear.services import ach_return_reason_service, audit_log_service
from openclear.services.ach_return_reason_service import AchReturnReasonInput, InvalidAchReturnReasonInput
from openclear.web.deps import WebNotFound, render_template, require_web_permission
from openclear.web.pagination import paginate
from openclear.web.redirects import see_other
from openclear.web.security import verify_csrf

router = APIRouter(prefix="/ui/ach-return-reasons", tags=["web-ach-return-reasons"])


@router.get("")
def list_ach_return_reasons(
    request: Request, page: int = 1, db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("ach_return_reason:manage")),
) -> HTMLResponse:
    page_obj = paginate(
        page=page,
        count_fn=lambda: AchReturnReasonRepository(db, ctx.tenant_id).count(),
        list_fn=lambda **kw: ach_return_reason_service.list_ach_return_reasons(
            db, ctx.tenant_id, order_by=AchReturnReason.created_at.desc(), **kw
        ),
    )
    return render_template(request, "ach_return_reasons/list.html", ctx=ctx, page_obj=page_obj)


@router.get("/new")
def new_ach_return_reason_form(
    request: Request, ctx: TenantContext = Depends(require_web_permission("ach_return_reason:manage"))
) -> HTMLResponse:
    return render_template(request, "ach_return_reasons/form.html", ctx=ctx, reason=None)


@router.post("")
def create_ach_return_reason(
    request: Request,
    reason_text: str = Form(...),
    transaction_code: str = Form(""),
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("ach_return_reason:manage")),
    _csrf: None = Depends(verify_csrf),
) -> HTMLResponse:
    try:
        reason = ach_return_reason_service.create_ach_return_reason(
            db, ctx.tenant_id, AchReturnReasonInput(reason_text=reason_text, transaction_code=transaction_code or None)
        )
    except InvalidAchReturnReasonInput as exc:
        db.rollback()
        return render_template(
            request, "ach_return_reasons/form.html", ctx=ctx, reason=None,
            reason_text=reason_text, transaction_code=transaction_code, error=str(exc), status_code=422,
        )

    audit_log_service.record_action(
        db, ctx.tenant_id, actor_user_id=ctx.user_id, channel="web", action="ach_return_reason.create",
        summary=f"Added ACH return reason {reason.reason_text!r}", resource_type="ach_return_reason", resource_id=reason.id,
    )
    db.commit()
    return see_other("/ui/ach-return-reasons", flash="Return reason created.")


@router.get("/{reason_id}/edit")
def edit_ach_return_reason_form(
    request: Request,
    reason_id: uuid.UUID,
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("ach_return_reason:manage")),
) -> HTMLResponse:
    reason = ach_return_reason_service.get_ach_return_reason(db, ctx.tenant_id, reason_id)
    if reason is None:
        raise WebNotFound()
    return render_template(request, "ach_return_reasons/form.html", ctx=ctx, reason=reason)


@router.post("/{reason_id}")
def update_ach_return_reason(
    request: Request,
    reason_id: uuid.UUID,
    reason_text: str = Form(...),
    transaction_code: str = Form(""),
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("ach_return_reason:manage")),
    _csrf: None = Depends(verify_csrf),
) -> HTMLResponse:
    try:
        reason = ach_return_reason_service.update_ach_return_reason(
            db, ctx.tenant_id, reason_id, AchReturnReasonInput(reason_text=reason_text, transaction_code=transaction_code or None)
        )
    except InvalidAchReturnReasonInput as exc:
        db.rollback()
        existing = ach_return_reason_service.get_ach_return_reason(db, ctx.tenant_id, reason_id)
        if existing is None:
            raise WebNotFound() from None
        return render_template(
            request, "ach_return_reasons/form.html", ctx=ctx, reason=existing,
            reason_text=reason_text, transaction_code=transaction_code, error=str(exc), status_code=422,
        )

    if reason is None:
        raise WebNotFound()

    audit_log_service.record_action(
        db, ctx.tenant_id, actor_user_id=ctx.user_id, channel="web", action="ach_return_reason.update",
        summary=f"Updated ACH return reason {reason.reason_text!r}", resource_type="ach_return_reason", resource_id=reason.id,
    )
    db.commit()
    return see_other("/ui/ach-return-reasons", flash="Return reason updated.")


@router.post("/{reason_id}/deactivate")
def deactivate_ach_return_reason(
    reason_id: uuid.UUID,
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("ach_return_reason:manage")),
    _csrf: None = Depends(verify_csrf),
) -> RedirectResponse:
    reason = ach_return_reason_service.deactivate_ach_return_reason(db, ctx.tenant_id, reason_id)
    if reason is not None:
        audit_log_service.record_action(
            db, ctx.tenant_id, actor_user_id=ctx.user_id, channel="web", action="ach_return_reason.deactivate",
            summary=f"Deactivated ACH return reason {reason.reason_text!r}", resource_type="ach_return_reason", resource_id=reason.id,
        )
    db.commit()
    if reason is None:
        return see_other("/ui/ach-return-reasons", error="Return reason not found.")
    return see_other("/ui/ach-return-reasons", flash="Return reason deactivated.")


@router.post("/{reason_id}/reactivate")
def reactivate_ach_return_reason(
    reason_id: uuid.UUID,
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("ach_return_reason:manage")),
    _csrf: None = Depends(verify_csrf),
) -> RedirectResponse:
    reason = ach_return_reason_service.reactivate_ach_return_reason(db, ctx.tenant_id, reason_id)
    if reason is not None:
        audit_log_service.record_action(
            db, ctx.tenant_id, actor_user_id=ctx.user_id, channel="web", action="ach_return_reason.reactivate",
            summary=f"Reactivated ACH return reason {reason.reason_text!r}", resource_type="ach_return_reason", resource_id=reason.id,
        )
    db.commit()
    if reason is None:
        return see_other("/ui/ach-return-reasons", error="Return reason not found.")
    return see_other("/ui/ach-return-reasons", flash="Return reason reactivated.")
