# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from openclear.api.v1.bulk_uploads import BulkUploadAccepted, accepted, too_many_for_sync
from openclear.api.v1.pagination import Page, page_params, set_page_headers
from openclear.auth.deps import require_permission
from openclear.db.session import get_db
from openclear.db.tenancy import TenantContext
from openclear.domain.bulk_upload_file import BulkUploadKind
from openclear.domain.paid_item import PaidItem, PaidItemSource
from openclear.networks.check.ingestion import PaidItemSubmission, ingest_paid_item, ingest_paid_items_bulk
from openclear.repositories.paid_item_repo import PaidItemRepository
from openclear.schemas.common import BulkRowResultOut, BulkSubmitResponse
from openclear.schemas.paid_item import PaidItemCreate, PaidItemRead
from openclear.services import audit_log_service, bulk_import_service
from openclear.workers.jobs import jobs_queued

router = APIRouter(prefix="/paid-items", tags=["paid-items"])


def _to_submission(payload: PaidItemCreate, source: PaidItemSource) -> PaidItemSubmission:
    return PaidItemSubmission(
        account_id=payload.account_id,
        check_number=payload.check_number,
        presented_amount=payload.presented_amount,
        presented_date=payload.presented_date,
        source=source,
    )


@router.post("", response_model=PaidItemRead, status_code=status.HTTP_201_CREATED)
def create_paid_item(
    payload: PaidItemCreate,
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_permission("paid_item:write")),
) -> PaidItemRead:
    try:
        item = ingest_paid_item(
            db, ctx.tenant_id, _to_submission(payload, PaidItemSource.API), scoped_customer_id=ctx.customer_id
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from None
    audit_log_service.record_action(
        db,
        ctx.tenant_id,
        actor_user_id=ctx.user_id,
        channel="api",
        action="paid_item.create",
        summary=f"Presented check #{item.check_number} for {item.presented_amount}",
        resource_type="paid_item",
        resource_id=item.id,
    )
    db.commit()
    return PaidItemRead.model_validate(item)


@router.post("/bulk", response_model=BulkSubmitResponse, responses={202: {"model": BulkUploadAccepted}})
def create_paid_items_bulk(
    payload: list[PaidItemCreate],
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_permission("paid_item:write")),
) -> BulkSubmitResponse:
    """Up to `bulk_api_sync_max_rows` items (1,000 by default) are imported now and every
    row's result returned. More than that answers `202` with an upload to poll at
    `/api/v1/bulk-uploads/{id}` while a background job imports them."""
    if too_many_for_sync(len(payload)):
        record = bulk_import_service.receive_api_items(
            db, ctx.tenant_id, kind=BulkUploadKind.PAID_ITEMS, items=payload, user_id=ctx.user_id, customer_id=ctx.customer_id
        )
        jobs_queued(db.get_bind())
        return JSONResponse(accepted(record).model_dump(mode="json"), status_code=status.HTTP_202_ACCEPTED)
    submissions = [_to_submission(row, PaidItemSource.BULK_FILE) for row in payload]
    results = ingest_paid_items_bulk(db, ctx.tenant_id, submissions, scoped_customer_id=ctx.customer_id)
    for r in results:
        if not r.success:
            continue
        audit_log_service.record_action(
            db,
            ctx.tenant_id,
            actor_user_id=ctx.user_id,
            channel="api",
            action="paid_item.create",
            summary=f"Paid item created via bulk API (row {r.index})",
            resource_type="paid_item",
            resource_id=r.paid_item_id,
        )
    db.commit()
    out = [
        BulkRowResultOut(index=r.index, success=r.success, id=str(r.paid_item_id) if r.paid_item_id else None, status=r.match_status, error=r.error)
        for r in results
    ]
    return BulkSubmitResponse(
        total=len(out), succeeded=sum(r.success for r in out), failed=sum(not r.success for r in out), results=out
    )


@router.get("", response_model=list[PaidItemRead])
def list_paid_items(
    request: Request,
    response: Response,
    account_id: uuid.UUID | None = None,
    page: Page = Depends(page_params),
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_permission("paid_item:read")),
) -> list[PaidItemRead]:
    repo = PaidItemRepository(db, ctx.tenant_id, ctx.customer_id)
    items = repo.list(account_id=account_id, order_by=(PaidItem.created_at, PaidItem.id), limit=page.limit, offset=page.offset)
    set_page_headers(request, response, page, repo.count(account_id=account_id))
    return [PaidItemRead.model_validate(i) for i in items]


@router.get("/{item_id}", response_model=PaidItemRead)
def get_paid_item(
    item_id: uuid.UUID,
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_permission("paid_item:read")),
) -> PaidItemRead:
    item = PaidItemRepository(db, ctx.tenant_id, ctx.customer_id).get(item_id)
    if item is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Paid item not found")
    return PaidItemRead.model_validate(item)
