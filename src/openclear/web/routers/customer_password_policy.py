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
from openclear.web.deps import WebNotFound, render_template, require_any_web_permission
from openclear.web.security import verify_csrf

router = APIRouter(prefix="/ui/customers/{customer_id}/password-policy", tags=["web-customer-password-policy"])
# The bank (customer:manage), or the customer's own administrators for their own customer
# while the bank lets it manage its sign-on (customer_sso:manage; CUSTOMER_ADMIN_PLAN.md CA-D8).
# Either way the policy can only be stricter than the bank's.
_allowed = require_any_web_permission("customer:manage", "customer_sso:manage")


def _get_customer_or_404(db: Session, tenant_id: uuid.UUID, customer_id: uuid.UUID, ctx: TenantContext | None = None):
    customer = customer_service.get_customer(db, tenant_id, customer_id)
    if customer is None:
        raise WebNotFound()
    if ctx is not None and ctx.customer_id is not None and (ctx.customer_id != customer_id or not customer.self_manages_sso):
        raise WebNotFound()
    return customer


def _render_form(request: Request, db: Session, ctx: TenantContext, customer_id: uuid.UUID, *, error: str | None = None, status_code: int = 200) -> HTMLResponse:
    customer = _get_customer_or_404(db, ctx.tenant_id, customer_id, ctx)
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
        tenant_requires_security_key=tenant.require_security_key,
        customer_requires_security_key=customer.require_security_key,
        error=error, status_code=status_code, back_url="/ui/sign-on" if ctx.customer_id else f"/ui/customers/{customer_id}",
    )


@router.get("")
def password_policy_form(
    request: Request, customer_id: uuid.UUID, db: Session = Depends(get_db),
    ctx: TenantContext = Depends(_allowed),
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
    ctx: TenantContext = Depends(_allowed),
    _csrf: None = Depends(verify_csrf),
) -> HTMLResponse:
    _get_customer_or_404(db, ctx.tenant_id, customer_id, ctx)

    try:
        parsed_min_length = int(min_length) if min_length.strip() else None
        if parsed_min_length is not None and parsed_min_length < 1:
            raise ValueError("Minimum password length must be at least 1")
        # Loosening the customer's own rules waits for a second person (DUAL_CONTROL_PLAN.md DC3).
        from openclear.services import change_request_service

        values = {"password_min_length": parsed_min_length, "password_require_uppercase": require_uppercase,
                  "password_require_lowercase": require_lowercase, "password_require_number": require_number,
                  "password_require_symbol": require_symbol}
        outcome = change_request_service.request_or_apply(db, "customer.password_policy", {"values": values}, tenant_id=ctx.tenant_id,
                                                          customer_id=customer_id, user_id=ctx.user_id)
    except ValueError as exc:
        db.rollback()
        return _render_form(request, db, ctx, customer_id, error=str(exc), status_code=422)
    if outcome.waiting:
        from urllib.parse import quote

        from openclear.web.routers.tenant_settings import waiting_message

        db.commit()
        return RedirectResponse(f"/ui/customers/{customer_id}/password-policy?flash={quote(waiting_message(outcome))}", status_code=303)

    audit_log_service.record_action(
        db, ctx.tenant_id, actor_user_id=ctx.user_id, channel="web", action="customer.update_password_policy",
        summary="Updated customer password policy", resource_type="customer", resource_id=customer_id,
    )
    db.commit()
    return RedirectResponse(f"/ui/customers/{customer_id}/password-policy?flash=Updated.", status_code=303)


@router.post("/security-keys")
def update_security_keys(
    request: Request,
    customer_id: uuid.UUID,
    require_security_key: bool = Form(False),
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(_allowed),
    _csrf: None = Depends(verify_csrf),
) -> HTMLResponse:
    """The customer requires security keys for its own people (OWASP_PLAN.md OW-D18): on applies at
    once; off waits for a second person."""
    from urllib.parse import quote

    from openclear.services import change_request_service
    from openclear.web.routers.tenant_settings import waiting_message

    _get_customer_or_404(db, ctx.tenant_id, customer_id, ctx)
    try:
        outcome = change_request_service.request_or_apply(
            db, "customer.security_keys", {"values": {"require_security_key": require_security_key}},
            tenant_id=ctx.tenant_id, customer_id=customer_id, user_id=ctx.user_id)
    except ValueError as exc:
        db.rollback()
        return _render_form(request, db, ctx, customer_id, error=str(exc), status_code=422)
    if outcome.waiting:
        db.commit()
        return RedirectResponse(f"/ui/customers/{customer_id}/password-policy?flash={quote(waiting_message(outcome))}", status_code=303)
    if outcome.summary != "Nothing changed.":
        audit_log_service.record_action(
            db, ctx.tenant_id, actor_user_id=ctx.user_id, channel="web", action="customer.update_security_keys",
            summary=f"Security keys {'required' if require_security_key else 'not required'} for the customer's people",
            resource_type="customer", resource_id=customer_id,
        )
    db.commit()
    return RedirectResponse(f"/ui/customers/{customer_id}/password-policy?flash=Updated.", status_code=303)
