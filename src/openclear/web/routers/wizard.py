# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import uuid

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from openclear.db.session import get_db
from openclear.db.tenancy import TenantContext
from openclear.services import customer_service, wizard_service
from openclear.web.deps import WebNotFound, render_template, require_web_permission
from openclear.web.security import verify_csrf

router = APIRouter(tags=["web-wizard"])


# --- Bank wizard: /ui/wizard/bank ---


@router.get("/ui/wizard/bank")
def bank_wizard(
    request: Request, db: Session = Depends(get_db), ctx: TenantContext = Depends(require_web_permission("tenant:manage"))
) -> HTMLResponse:
    steps = wizard_service.get_bank_wizard_steps(db, ctx.tenant_id)
    required = [v for v in steps if not v.step.optional]
    return render_template(
        request, "wizard/bank.html", ctx=ctx, steps=steps,
        required_done=sum(1 for v in required if v.is_complete), required_total=len(required),
    )


@router.post("/ui/wizard/bank/{step_key}/acknowledge")
def acknowledge_bank_step(
    step_key: str, db: Session = Depends(get_db), ctx: TenantContext = Depends(require_web_permission("tenant:manage")),
    _csrf: None = Depends(verify_csrf),
) -> RedirectResponse:
    wizard_service.acknowledge_step(db, ctx.tenant_id, None, step_key, ctx.user_id)
    db.commit()
    return RedirectResponse("/ui/wizard/bank", status_code=303)


@router.post("/ui/wizard/bank/{step_key}/unacknowledge")
def unacknowledge_bank_step(
    step_key: str, db: Session = Depends(get_db), ctx: TenantContext = Depends(require_web_permission("tenant:manage")),
    _csrf: None = Depends(verify_csrf),
) -> RedirectResponse:
    wizard_service.unacknowledge_step(db, ctx.tenant_id, None, step_key)
    db.commit()
    return RedirectResponse("/ui/wizard/bank", status_code=303)


# --- Customer wizard: /ui/customers/{customer_id}/wizard ---


def _get_customer_or_404(db: Session, tenant_id: uuid.UUID, customer_id: uuid.UUID):
    customer = customer_service.get_customer(db, tenant_id, customer_id)
    if customer is None:
        raise WebNotFound()
    return customer


@router.get("/ui/customers/{customer_id}/wizard")
def customer_wizard(
    request: Request, customer_id: uuid.UUID, db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("customer:manage")),
) -> HTMLResponse:
    customer = _get_customer_or_404(db, ctx.tenant_id, customer_id)
    steps = wizard_service.get_customer_wizard_steps(db, ctx.tenant_id, customer_id)
    required = [v for v in steps if not v.step.optional]
    return render_template(
        request, "wizard/customer.html", ctx=ctx, customer=customer, steps=steps,
        required_done=sum(1 for v in required if v.is_complete), required_total=len(required),
    )


@router.post("/ui/customers/{customer_id}/wizard/{step_key}/acknowledge")
def acknowledge_customer_step(
    customer_id: uuid.UUID, step_key: str, db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("customer:manage")), _csrf: None = Depends(verify_csrf),
) -> RedirectResponse:
    _get_customer_or_404(db, ctx.tenant_id, customer_id)
    wizard_service.acknowledge_step(db, ctx.tenant_id, customer_id, step_key, ctx.user_id)
    db.commit()
    return RedirectResponse(f"/ui/customers/{customer_id}/wizard", status_code=303)


@router.post("/ui/customers/{customer_id}/wizard/{step_key}/unacknowledge")
def unacknowledge_customer_step(
    customer_id: uuid.UUID, step_key: str, db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("customer:manage")), _csrf: None = Depends(verify_csrf),
) -> RedirectResponse:
    _get_customer_or_404(db, ctx.tenant_id, customer_id)
    wizard_service.unacknowledge_step(db, ctx.tenant_id, customer_id, step_key)
    db.commit()
    return RedirectResponse(f"/ui/customers/{customer_id}/wizard", status_code=303)
