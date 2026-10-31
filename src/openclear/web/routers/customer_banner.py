# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session
import uuid

from openclear.db.session import get_db
from openclear.db.tenancy import TenantContext
from openclear.services import audit_log_service, customer_service
from openclear.web.deps import WebNotFound, render_template, require_web_permission
from openclear.web.redirects import see_other
from openclear.web.security import verify_csrf
from openclear.web.errors import shown

router = APIRouter(prefix="/ui/customers/{customer_id}/banner", tags=["web-customer-banner"])


def _get_customer_or_404(db: Session, ctx: TenantContext, customer_id: uuid.UUID):
    # customer_banner:manage is deliberately NOT masked out of a customer-scoped session
    # (see auth/permissions.py) -- unlike every other customer-scoped route in this app,
    # a customer-scoped caller can genuinely reach this one. This is the one extra check
    # that keeps it self-service-only-for-your-own-customer: a customer-scoped session's
    # own customer_id must match the path's, or this is someone else's customer -- same
    # "can't touch another tenant's/customer's resource" 404 posture used everywhere else
    # in this app (never a distinguishable 403, which would confirm the resource exists).
    if ctx.customer_id is not None and ctx.customer_id != customer_id:
        raise WebNotFound()
    customer = customer_service.get_customer(db, ctx.tenant_id, customer_id)
    if customer is None:
        raise WebNotFound()
    return customer


@router.get("")
def banner_form(
    request: Request, customer_id: uuid.UUID, db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("customer_banner:manage")),
) -> HTMLResponse:
    customer = _get_customer_or_404(db, ctx, customer_id)
    return render_template(
        request, "customers/banner.html", ctx=ctx, customer=customer, banner_message=customer.banner_message or "",
    )


@router.post("")
def update_banner(
    request: Request,
    customer_id: uuid.UUID,
    banner_message: str = Form(""),
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("customer_banner:manage")),
    _csrf: None = Depends(verify_csrf),
) -> HTMLResponse:
    customer = _get_customer_or_404(db, ctx, customer_id)

    try:
        customer_service.set_banner_message(db, ctx.tenant_id, customer_id, banner_message=banner_message)
    except ValueError as exc:
        db.rollback()
        return render_template(
            request, "customers/banner.html", ctx=ctx, customer=customer, banner_message=banner_message,
            error=shown(exc), status_code=422,
        )

    audit_log_service.record_action(
        db, ctx.tenant_id, actor_user_id=ctx.user_id, channel="web", action="customer.update_banner_message",
        summary="Updated customer banner message", resource_type="customer", resource_id=customer_id,
    )
    db.commit()
    return see_other(f"/ui/customers/{customer_id}/banner", flash="Updated.")
