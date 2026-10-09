# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""The status and row results of a bulk upload imported in the background
(SCALE_PLAN.md S4): a JSON bulk call over the row cap answers 202 with one of these to
poll, and every web and dropbox upload has one too. A file in any import format the bank
can use, built in or from a plugin (PLUGIN_PLAN.md PL2), can also be sent here directly."""

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, Response, UploadFile, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from openclear.api.v1.pagination import Page, page_params, set_page_headers
from openclear.auth.deps import get_current_context
from openclear.config import get_settings
from openclear.db.session import get_db
from openclear.db.tenancy import TenantContext
from openclear.domain.bulk_upload_file import BulkUploadFile, BulkUploadKind, BulkUploadSource
from openclear.services import bulk_import_service, bulk_upload_file_service, plugin_service
from openclear.workers.jobs import jobs_queued

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


class ImportFormatRead(BaseModel):
    kind: str
    format: str
    built_in: bool


@router.get("/formats", response_model=list[ImportFormatRead])
def list_import_formats(db: Session = Depends(get_db), ctx: TenantContext = Depends(get_current_context)) -> list[ImportFormatRead]:
    """The import formats this organization can send to `POST /api/v1/bulk-uploads`: the
    built-in ones and any a plugin provides that an admin has turned on, limited to the
    kinds the caller may upload."""
    from openclear import plugins

    return [
        ImportFormatRead(kind=kind.value, format=fmt, built_in=plugins.is_builtin("import_formats", (kind, fmt)))
        for kind, fmt in plugin_service.enabled(db, ctx.tenant_id, "import_formats")
        if bulk_import_service.PERMISSION_BY_KIND.get(kind) in ctx.permissions
    ]


@router.post("", response_model=BulkUploadAccepted, status_code=status.HTTP_202_ACCEPTED)
def upload_file(
    kind: BulkUploadKind = Form(...),
    format: str = Form(..., description="An import format from GET /api/v1/bulk-uploads/formats"),
    file: UploadFile = File(...),
    create_missing_accounts: bool = Form(False),
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(get_current_context),
) -> BulkUploadAccepted:
    """Uploads a file to import in the background. Poll `status_url` for progress. A file
    that can't be read at all comes back `rejected`, with the reason in `error`."""
    permission = bulk_import_service.PERMISSION_BY_KIND.get(kind)
    if permission not in ctx.permissions:
        raise HTTPException(status.HTTP_403_FORBIDDEN, f"Missing permission: {permission}")
    try:
        record = bulk_import_service.receive_upload(
            db, ctx.tenant_id, kind=kind, file_format=format, filename=file.filename or f"upload.{format}",
            content_type=file.content_type, uploaded_by_user_id=ctx.user_id, customer_id=ctx.customer_id,
            source=BulkUploadSource.API, stream=file.file, options={"create_missing_accounts": create_missing_accounts},
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from None
    jobs_queued(db.get_bind())
    return accepted(record)


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
