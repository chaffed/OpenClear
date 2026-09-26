# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

from openclear.api.v1.pagination import Page, page_params, set_page_headers
from openclear.auth.deps import require_permission
from openclear.db.session import get_db
from openclear.db.tenancy import TenantContext
from openclear.domain.issued_item import IssuedItem, IssuedItemStatus
from openclear.repositories.issued_item_repo import IssuedItemRepository
from openclear.schemas.common import BulkRowResultOut, BulkSubmitResponse
from openclear.schemas.issued_item import IssuedItemCreate, IssuedItemRead, IssuedItemVoidRequest
from openclear.services import audit_log_service, issued_item_service

router = APIRouter(prefix="/issued-items", tags=["issued-items"])


@router.post("", response_model=IssuedItemRead, status_code=status.HTTP_201_CREATED)
def create_issued_item(
    payload: IssuedItemCreate,
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_permission("issued_item:write")),
) -> IssuedItemRead:
    try:
        item = issued_item_service.create_issued_item(
            db,
            ctx.tenant_id,
            issued_item_service.IssuedItemInput(**payload.model_dump()),
            submitted_by_user_id=ctx.user_id,
            scoped_customer_id=ctx.customer_id,
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from None
    audit_log_service.record_action(
        db,
        ctx.tenant_id,
        actor_user_id=ctx.user_id,
        channel="api",
        action="issued_item.create",
        summary=f"Issued check #{item.check_number} for {item.amount} to {item.payee_name}",
        resource_type="issued_item",
        resource_id=item.id,
    )
    db.commit()
    return IssuedItemRead.model_validate(item)


@router.post("/bulk", response_model=BulkSubmitResponse)
def create_issued_items_bulk(
    payload: list[IssuedItemCreate],
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_permission("issued_item:write")),
) -> BulkSubmitResponse:
    inputs = [issued_item_service.IssuedItemInput(**row.model_dump()) for row in payload]
    results = issued_item_service.create_issued_items_bulk(
        db, ctx.tenant_id, inputs, submitted_by_user_id=ctx.user_id, scoped_customer_id=ctx.customer_id
    )
    for r in results:
        if not r.success:
            continue
        audit_log_service.record_action(
            db,
            ctx.tenant_id,
            actor_user_id=ctx.user_id,
            channel="api",
            action="issued_item.create",
            summary=f"Issued item created via bulk API (row {r.index})",
            resource_type="issued_item",
            resource_id=r.issued_item_id,
        )
    db.commit()
    out = [
        BulkRowResultOut(
            index=r.index,
            success=r.success,
            id=str(r.issued_item_id) if r.issued_item_id else None,
            error=r.error,
        )
        for r in results
    ]
    return BulkSubmitResponse(
        total=len(out), succeeded=sum(r.success for r in out), failed=sum(not r.success for r in out), results=out
    )


@router.get("", response_model=list[IssuedItemRead])
def list_issued_items(
    request: Request,
    response: Response,
    status_filter: IssuedItemStatus | None = None,
    account_id: uuid.UUID | None = None,
    page: Page = Depends(page_params),
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_permission("issued_item:read")),
) -> list[IssuedItemRead]:
    items = issued_item_service.list_issued_items(
        db, ctx.tenant_id, status=status_filter, account_id=account_id, customer_id=ctx.customer_id,
        order_by=(IssuedItem.created_at, IssuedItem.id), limit=page.limit, offset=page.offset,
    )
    total = IssuedItemRepository(db, ctx.tenant_id, ctx.customer_id).count(status=status_filter, account_id=account_id)
    set_page_headers(request, response, page, total)
    return [IssuedItemRead.model_validate(i) for i in items]


@router.get("/outstanding", response_model=list[IssuedItemRead])
def list_outstanding_issued_items(
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_permission("issued_item:read")),
) -> list[IssuedItemRead]:
    items = issued_item_service.list_issued_items(
        db, ctx.tenant_id, status=IssuedItemStatus.OUTSTANDING, customer_id=ctx.customer_id
    )
    return [IssuedItemRead.model_validate(i) for i in items]


@router.get("/{item_id}", response_model=IssuedItemRead)
def get_issued_item(
    item_id: uuid.UUID,
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_permission("issued_item:read")),
) -> IssuedItemRead:
    item = IssuedItemRepository(db, ctx.tenant_id, ctx.customer_id).get(item_id)
    if item is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Issued item not found")
    return IssuedItemRead.model_validate(item)


@router.patch("/{item_id}/void", response_model=IssuedItemRead)
def void_issued_item(
    item_id: uuid.UUID,
    payload: IssuedItemVoidRequest,
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_permission("issued_item:write")),
) -> IssuedItemRead:
    item = issued_item_service.void_issued_item(db, ctx.tenant_id, item_id, payload.reason, scoped_customer_id=ctx.customer_id)
    if item is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Issued item not found")
    audit_log_service.record_action(
        db,
        ctx.tenant_id,
        actor_user_id=ctx.user_id,
        channel="api",
        action="issued_item.void",
        summary=f"Voided check #{item.check_number}: {payload.reason}",
        resource_type="issued_item",
        resource_id=item.id,
    )
    db.commit()
    return IssuedItemRead.model_validate(item)
