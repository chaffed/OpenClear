# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

from openclear.api.v1.pagination import Page, page_params, set_page_headers
from openclear.auth.deps import require_permission
from openclear.db.session import get_db
from openclear.db.tenancy import TenantContext
from openclear.domain.exception_item import ExceptionItem
from openclear.domain.exception_item import ExceptionStatus
from openclear.schemas.exception import ExceptionRead
from openclear.services import exception_service

router = APIRouter(prefix="/exceptions", tags=["exceptions"])


@router.get("", response_model=list[ExceptionRead])
def list_exceptions(
    request: Request,
    response: Response,
    network_code: str | None = None,
    status_filter: ExceptionStatus | None = None,
    page: Page = Depends(page_params),
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_permission("exception:read")),
) -> list[ExceptionRead]:
    statuses = (status_filter,) if status_filter else None
    total = exception_service.count_exceptions(
        db, ctx.tenant_id, network_code=network_code, statuses=statuses, customer_id=ctx.customer_id
    )
    items = exception_service.list_exceptions(
        db, ctx.tenant_id, network_code=network_code, status=status_filter, customer_id=ctx.customer_id,
        order_by=(ExceptionItem.created_at, ExceptionItem.id), limit=page.limit, offset=page.offset,
    )
    set_page_headers(request, response, page, total)
    return [ExceptionRead.from_orm_row(i) for i in items]


@router.get("/{exception_id}", response_model=ExceptionRead)
def get_exception(
    exception_id: uuid.UUID,
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_permission("exception:read")),
) -> ExceptionRead:
    item = exception_service.get_exception(db, ctx.tenant_id, exception_id, customer_id=ctx.customer_id)
    if item is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Exception not found")
    return ExceptionRead.from_orm_row(item)
