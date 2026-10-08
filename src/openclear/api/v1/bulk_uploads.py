# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""The status and row results of a bulk upload imported in the background
(SCALE_PLAN.md S4): a JSON bulk call over the row cap answers 202 with one of these to
poll, and every web and dropbox upload has one too."""

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from openclear.api.v1.pagination import Page, page_params, set_page_headers
from openclear.auth.deps import get_current_context
from openclear.config import get_settings
from openclear.db.session import get_db
from openclear.db.tenancy import TenantContext
from openclear.domain.bulk_upload_file import BulkUploadFile
from openclear.services import bulk_import_service, bulk_upload_file_service

router = APIRouter(prefix="/bulk-uploads", tags=["bulk-uploads"])


class BulkUploadRowRead(BaseModel):
    index: int
    label: str
    success: bool
    id: uuid.UUID | None = None
    error: str | None = None


class BulkUploadRead(BaseModel):
    id: uuid.UUID
    kind: str
    source: str
    status: str
    filename: str
    total_rows: int | None
    processed_rows: int
    succeeded: int | None
    failed: int | None
    error: str | None
    uploaded_at: datetime
    completed_at: datetime | None


class BulkUploadAccepted(BaseModel):
    upload_id: uuid.UUID
    status: str
    total: int
    status_url: str


def upload_read(record: BulkUploadFile) -> BulkUploadRead:
    return BulkUploadRead(
        id=record.id, kind=record.kind.value, source=record.source.value, status=record.status.value,
        filename=record.original_filename, total_rows=record.total_rows, processed_rows=record.processed_rows or 0,
        succeeded=record.succeeded_count, failed=record.failed_count, error=record.error,
        uploaded_at=record.uploaded_at, completed_at=record.completed_at,
    )


def accepted(record: BulkUploadFile) -> BulkUploadAccepted:
    return BulkUploadAccepted(
        upload_id=record.id, status=record.status.value, total=record.total_rows or 0, status_url=f"/api/v1/bulk-uploads/{record.id}"
    )


def too_many_for_sync(count: int) -> bool:
    return count > get_settings().bulk_api_sync_max_rows


def _authorized(db: Session, ctx: TenantContext, upload_id: uuid.UUID) -> BulkUploadFile:
    record = bulk_upload_file_service.get_uploaded_file(db, ctx.tenant_id, upload_id, ctx.customer_id)
    if record is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Upload not found")
    permission = bulk_import_service.PERMISSION_BY_KIND[record.kind]
    if permission not in ctx.permissions:
        raise HTTPException(status.HTTP_403_FORBIDDEN, f"Missing permission: {permission}")
    return record


@router.get("/{upload_id}", response_model=BulkUploadRead)
def get_bulk_upload(upload_id: uuid.UUID, db: Session = Depends(get_db), ctx: TenantContext = Depends(get_current_context)) -> BulkUploadRead:
    """Where the import is: `queued`, `processing` (`processed_rows` of `total_rows`),
    `completed`, `rejected` (nothing imported; see `error`) or `failed`."""
    return upload_read(bulk_import_service.check_job(db, _authorized(db, ctx, upload_id)))


@router.get("/{upload_id}/results", response_model=list[BulkUploadRowRead])
def get_bulk_upload_results(
    upload_id: uuid.UUID,
    request: Request,
    response: Response,
    failures_only: bool = False,
    page: Page = Depends(page_params),
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(get_current_context),
) -> list[BulkUploadRowRead]:
    """Each imported row's outcome, in file order, paged. `index` is the row's position in
    the request (0-based) or file."""
    record = _authorized(db, ctx, upload_id)
    rows = bulk_import_service.row_results(db, record, failures_only=failures_only, limit=page.limit, offset=page.offset)
    total = (record.failed_count or 0) if failures_only else (record.processed_rows or 0)
    set_page_headers(request, response, page, total)
    return [BulkUploadRowRead(index=r.row_index, label=r.row_label, success=r.success, id=r.created_id, error=r.error) for r in rows]
