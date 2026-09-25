# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session
import uuid

from openclear.db.session import get_db
from openclear.db.tenancy import TenantContext
from openclear.domain.tenant import Tenant
from openclear.services import audit_log_service, customer_service
from openclear.web.deps import WebNotFound, render_template, require_web_permission
from openclear.web.security import verify_csrf

router = APIRouter(prefix="/ui/customers/{customer_id}/password-policy", tags=["web-customer-password-policy"])


def _get_customer_or_404(db: Session, tenant_id: uuid.UUID, customer_id: uuid.UUID):
    customer = customer_service.get_customer(db, tenant_id, customer_id)
    if customer is None:
        raise WebNotFound()
    return customer


def _render_form(request: Request, db: Session, ctx: TenantContext, customer_id: uuid.UUID, *, error: str | None = None, status_code: int = 200) -> HTMLResponse:
    customer = _get_customer_or_404(db, ctx.tenant_id, customer_id)
    tenant = db.get(Tenant, ctx.tenant_id)
    return render_template(
        request, "customers/password_policy.html", ctx=ctx, customer=customer,
        tenant_min_length=tenant.password_min_length,
        tenant_require_uppercase=tenant.password_require_uppercase,
        tenant_require_lowercase=tenant.password_require_lowercase,
        tenant_require_number=tenant.password_require_number,
        tenant_require_symbol=tenant.password_require_symbol,
        customer_min_length=customer.password_min_length,
        customer_require_uppercase=customer.password_require_uppercase,
        customer_require_lowercase=customer.password_require_lowercase,
        customer_require_number=customer.password_require_number,
        customer_require_symbol=customer.password_require_symbol,
        error=error, status_code=status_code,
    )


@router.get("")
def password_policy_form(
    request: Request, customer_id: uuid.UUID, db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("customer:manage")),
) -> HTMLResponse:
    return _render_form(request, db, ctx, customer_id)


@router.post("")
def update_password_policy(
    request: Request,
    customer_id: uuid.UUID,
    min_length: str = Form(""),
    require_uppercase: bool = Form(False),
    require_lowercase: bool = Form(False),
    require_number: bool = Form(False),
    require_symbol: bool = Form(False),
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("customer:manage")),
    _csrf: None = Depends(verify_csrf),
) -> HTMLResponse:
    _get_customer_or_404(db, ctx.tenant_id, customer_id)

    try:
        parsed_min_length = int(min_length) if min_length.strip() else None
        if parsed_min_length is not None and parsed_min_length < 1:
            raise ValueError("Minimum password length must be at least 1")
        customer_service.set_password_policy(
            db, ctx.tenant_id, customer_id,
            min_length=parsed_min_length, require_uppercase=require_uppercase, require_lowercase=require_lowercase,
            require_number=require_number, require_symbol=require_symbol,
        )
    except ValueError as exc:
        db.rollback()
        return _render_form(request, db, ctx, customer_id, error=str(exc), status_code=422)

    audit_log_service.record_action(
        db, ctx.tenant_id, actor_user_id=ctx.user_id, channel="web", action="customer.update_password_policy",
        summary="Updated customer password policy", resource_type="customer", resource_id=customer_id,
    )
    db.commit()
    return RedirectResponse(f"/ui/customers/{customer_id}/password-policy?flash=Updated.", status_code=303)
