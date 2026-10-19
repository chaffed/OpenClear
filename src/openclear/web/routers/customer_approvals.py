# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Approvals → New customer users (MOBILE_PLAN.md R6; services/customer_approval_service.py):
the bank approves or rejects people a customer's side added, when it asks to."""

import uuid
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from openclear.db.session import get_db
from openclear.db.tenancy import TenantContext
from openclear.services import audit_log_service, customer_approval_service
from openclear.services.customer_approval_service import ApprovalError
from openclear.web.deps import render_template, require_web_permission
from openclear.web.security import verify_csrf
from openclear.web.redirects import see_other

router = APIRouter(prefix="/ui/approvals/customer-users", tags=["web-customer-approvals"])
_manage = require_web_permission("customer:manage")


def _bank_only(ctx: TenantContext = Depends(_manage)) -> TenantContext:
    from openclear.web.deps import WebForbidden

    if ctx.customer_id is not None:
        raise WebForbidden()
    return ctx


def _back(message: str) -> RedirectResponse:
    return see_other("/ui/approvals/customer-users?flash=" + quote(message))


@router.get("")
def waiting(request: Request, db: Session = Depends(get_db), ctx: TenantContext = Depends(_bank_only)) -> HTMLResponse:
    return render_template(request, "approvals/customer_users.html", ctx=ctx, rows=customer_approval_service.waiting(db, ctx.tenant_id))


def _decide(db: Session, ctx: TenantContext, membership_id: uuid.UUID, *, approved: bool, reason: str = "") -> RedirectResponse:
    from openclear.domain.tenant_membership import TenantMembership
    from openclear.domain.user import User

    found = db.get(TenantMembership, membership_id)
    email = db.get(User, found.user_id).email if found is not None else ""
    customer_id = found.customer_id if found is not None else None
    try:
        if approved:
            customer_approval_service.approve(db, ctx.tenant_id, membership_id)
        else:
            customer_approval_service.reject(db, ctx.tenant_id, membership_id, reason)
    except ApprovalError as exc:
        db.rollback()
        return _back(str(exc))
    from openclear.domain.customer import Customer

    customer = db.get(Customer, customer_id)
    audit_log_service.record_action(
        db, ctx.tenant_id, actor_user_id=ctx.user_id, channel="web",
        action="customer_user.approve" if approved else "customer_user.reject",
        summary=(f"Approved {email} for {customer.name}" if approved else f"Rejected {email} for {customer.name}: {reason.strip()}"),
        resource_type="customer", resource_id=customer_id, metadata={"email": email, "membership_id": str(membership_id)},
    )
    customer_approval_service.tell_administrators(db, ctx.tenant_id, customer_id, email=email, approved=approved, reason=reason.strip())
    db.commit()
    return _back(f"{email} approved." if approved else f"{email} rejected; {customer.name}'s administrators have been told.")


@router.post("/{membership_id}/approve")
def approve(membership_id: uuid.UUID, db: Session = Depends(get_db), ctx: TenantContext = Depends(_bank_only),
            _csrf: None = Depends(verify_csrf)) -> RedirectResponse:
    return _decide(db, ctx, membership_id, approved=True)


@router.post("/{membership_id}/reject")
def reject(membership_id: uuid.UUID, reason: str = Form(""), db: Session = Depends(get_db), ctx: TenantContext = Depends(_bank_only),
           _csrf: None = Depends(verify_csrf)) -> RedirectResponse:
    return _decide(db, ctx, membership_id, approved=False, reason=reason)
