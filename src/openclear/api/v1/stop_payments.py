# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

from openclear.api.v1.pagination import Page, page_params, set_page_headers
from openclear.auth.deps import require_permission
from openclear.db.session import get_db
from openclear.db.tenancy import TenantContext
from openclear.domain.stop_payment import StopPayment, StopPaymentStatus
from openclear.repositories.stop_payment_repo import StopPaymentRepository
from openclear.schemas.stop_payment import StopPaymentCreate, StopPaymentRead
from openclear.services import audit_log_service, stop_payment_service

router = APIRouter(prefix="/stop-payments", tags=["stop-payments"])


@router.post("", response_model=StopPaymentRead, status_code=status.HTTP_201_CREATED)
def create_stop_payment(
    payload: StopPaymentCreate,
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_permission("stop_payment:write")),
) -> StopPaymentRead:
    try:
        stop = stop_payment_service.create_stop_payment(
            db,
            ctx.tenant_id,
            stop_payment_service.StopPaymentInput(**payload.model_dump()),
            created_by_user_id=ctx.user_id,
            scoped_customer_id=ctx.customer_id,
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from None
    audit_log_service.record_action(
        db,
        ctx.tenant_id,
        actor_user_id=ctx.user_id,
        channel="api",
        action="stop_payment.create",
        summary=f"Stopped payment on check #{stop.check_number}" + (f": {stop.reason}" if stop.reason else ""),
        resource_type="stop_payment",
        resource_id=stop.id,
    )
    db.commit()
    return StopPaymentRead.model_validate(stop)


@router.get("", response_model=list[StopPaymentRead])
def list_stop_payments(
    request: Request,
    response: Response,
    status_filter: StopPaymentStatus | None = None,
    page: Page = Depends(page_params),
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_permission("stop_payment:read")),
) -> list[StopPaymentRead]:
    stops = stop_payment_service.list_stop_payments(
        db, ctx.tenant_id, status=status_filter, customer_id=ctx.customer_id,
        order_by=(StopPayment.created_at, StopPayment.id), limit=page.limit, offset=page.offset,
    )
    total = StopPaymentRepository(db, ctx.tenant_id, ctx.customer_id).count(status=status_filter)
    set_page_headers(request, response, page, total)
    return [StopPaymentRead.model_validate(s) for s in stops]


@router.get("/outstanding", response_model=list[StopPaymentRead])
def list_active_stop_payments(
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_permission("stop_payment:read")),
) -> list[StopPaymentRead]:
    stops = stop_payment_service.list_stop_payments(
        db, ctx.tenant_id, status=StopPaymentStatus.ACTIVE, customer_id=ctx.customer_id
    )
    return [StopPaymentRead.model_validate(s) for s in stops]


@router.patch("/{stop_id}/cancel", response_model=StopPaymentRead)
def cancel_stop_payment(
    stop_id: uuid.UUID,
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_permission("stop_payment:write")),
) -> StopPaymentRead:
    stop = stop_payment_service.cancel_stop_payment(db, ctx.tenant_id, stop_id, scoped_customer_id=ctx.customer_id)
    if stop is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Stop payment not found")
    audit_log_service.record_action(
        db,
        ctx.tenant_id,
        actor_user_id=ctx.user_id,
        channel="api",
        action="stop_payment.cancel",
        summary=f"Cancelled stop payment on check #{stop.check_number}",
        resource_type="stop_payment",
        resource_id=stop.id,
    )
    db.commit()
    return StopPaymentRead.model_validate(stop)
