# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import uuid
from datetime import date
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from openclear.db.session import get_db
from openclear.db.tenancy import TenantContext
from openclear.domain.stop_payment import StopPayment
from openclear.repositories.stop_payment_repo import StopPaymentRepository
from openclear.services import account_service, audit_log_service, stop_payment_service
from openclear.web.deps import render_template, require_web_permission
from openclear.web.pagination import paginate
from openclear.web.form_errors import friendly_error
from openclear.web.redirects import see_other
from openclear.web.security import verify_csrf

router = APIRouter(prefix="/ui/stop-payments", tags=["web-stop-payments"])


@router.get("")
def list_stop_payments(
    request: Request, page: int = 1, status: str = "", number: str = "", db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("stop_payment:read")),
) -> HTMLResponse:
    """Filtered by status (active by default once there are expired or cancelled ones to hide)
    and check number (FULL_REVIEW_PLAN.md FR-D14)."""
    from openclear.domain.stop_payment import StopPaymentStatus

    chosen = StopPaymentStatus(status) if status in {s.value for s in StopPaymentStatus} else None
    number = number.strip()[:32]
    filters = {"status": chosen, "check_number": number or None}
    repo = StopPaymentRepository(db, ctx.tenant_id, ctx.customer_id)
    page_obj = paginate(
        page=page,
        count_fn=lambda: repo.count(**filters),
        list_fn=lambda **kw: repo.list(order_by=StopPayment.created_at.desc(), **filters, **kw),
    )
    return render_template(request, "stop_payments/list.html", ctx=ctx, page_obj=page_obj, status_filter=status if chosen else "",
                           number_filter=number, statuses=[s.value for s in StopPaymentStatus])


@router.get("/new")
def new_stop_payment_form(
    request: Request,
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("stop_payment:write")),
) -> HTMLResponse:
    accounts = account_service.list_accounts(db, ctx.tenant_id, customer_id=ctx.customer_id)
    # Effective today unless changed (UI_PLAN.md FL6): nearly every stop payment is.
    return render_template(request, "stop_payments/form.html", ctx=ctx, accounts=accounts,
                           form={"effective_date": date.today().isoformat()})


@router.post("")
def create_stop_payment(
    request: Request,
    account_id: uuid.UUID = Form(...),
    check_number: str = Form(...),
    amount: str = Form(""),
    effective_date: str = Form(...),
    expiration_date: str = Form(""),
    reason: str = Form(""),
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("stop_payment:write")),
    _csrf: None = Depends(verify_csrf),
) -> HTMLResponse:
    # What was typed comes back with an error, rather than an empty form.
    form = {"account_id": str(account_id), "check_number": check_number, "amount": amount, "effective_date": effective_date,
            "expiration_date": expiration_date, "reason": reason}
    try:
        parsed_amount = Decimal(amount) if amount else None
        parsed_effective = date.fromisoformat(effective_date)
        parsed_expiration = date.fromisoformat(expiration_date) if expiration_date else None
    except (InvalidOperation, ValueError):
        accounts = account_service.list_accounts(db, ctx.tenant_id, customer_id=ctx.customer_id)
        return render_template(
            request, "stop_payments/form.html", ctx=ctx, accounts=accounts, form=form, error="Invalid amount or date.", status_code=422
        )

    try:
        stop = stop_payment_service.create_stop_payment(
            db,
            ctx.tenant_id,
            stop_payment_service.StopPaymentInput(
                account_id=account_id,
                check_number=check_number,
                amount=parsed_amount,
                effective_date=parsed_effective,
                expiration_date=parsed_expiration,
                reason=reason or None,
            ),
            created_by_user_id=ctx.user_id,
            scoped_customer_id=ctx.customer_id,
        )
    except Exception as exc:  # noqa: BLE001 — surface e.g. an out-of-scope account to the form
        db.rollback()
        accounts = account_service.list_accounts(db, ctx.tenant_id, customer_id=ctx.customer_id)
        return render_template(
            request,
            "stop_payments/form.html",
            ctx=ctx,
            accounts=accounts,
            form=form,
            error=friendly_error(exc, action="Could not create stop payment"),
            status_code=422,
        )
    audit_log_service.record_action(
        db,
        ctx.tenant_id,
        actor_user_id=ctx.user_id,
        channel="web",
        action="stop_payment.create",
        summary=f"Stopped payment on check #{stop.check_number}" + (f": {reason}" if reason else ""),
        resource_type="stop_payment",
        resource_id=stop.id,
    )
    db.commit()
    return see_other("/ui/stop-payments", flash="Stop payment created.")


@router.post("/{stop_id}/cancel")
def cancel_stop_payment(
    stop_id: uuid.UUID,
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("stop_payment:write")),
    _csrf: None = Depends(verify_csrf),
) -> RedirectResponse:
    stop = stop_payment_service.cancel_stop_payment(db, ctx.tenant_id, stop_id, scoped_customer_id=ctx.customer_id)
    if stop is not None:
        audit_log_service.record_action(
            db,
            ctx.tenant_id,
            actor_user_id=ctx.user_id,
            channel="web",
            action="stop_payment.cancel",
            summary=f"Cancelled stop payment on check #{stop.check_number}",
            resource_type="stop_payment",
            resource_id=stop.id,
        )
    db.commit()
    return see_other("/ui/stop-payments", flash="Stop payment cancelled.")
