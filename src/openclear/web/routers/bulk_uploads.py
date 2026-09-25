# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import uuid
from urllib.parse import quote

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.orm import Session

from openclear.bulk_import.file_storage import read_uploaded_file
from openclear.db.session import get_db
from openclear.db.tenancy import TenantContext
from openclear.domain.bulk_upload_file import BulkUploadFile, BulkUploadKind
from openclear.services import bulk_upload_file_service, bulk_upload_reversal_service
from openclear.web.deps import WebForbidden, WebNotFound, get_web_context, render_template
from openclear.web.security import verify_csrf

router = APIRouter(prefix="/ui/bulk-uploads", tags=["web-bulk-uploads"])

# One route serves all three upload kinds, so the permission to check is looked up per
# record rather than fixed at the dependency level — the same permission that gated the
# original upload gates viewing/downloading it later.
_PERMISSION_BY_KIND = {
    BulkUploadKind.ISSUED_ITEMS: "issued_item:write",
    BulkUploadKind.ACH_TRANSACTIONS: "ach_transaction:write",
    BulkUploadKind.USERS: "user:manage",
    BulkUploadKind.ACCOUNTS: "account:write",
    BulkUploadKind.CHECK_IMAGES: "check_image:write",
}

# Backing out a CHECK_IMAGES upload can create/revert paid_item and issued_item state,
# not just check_image state (see bulk_upload_reversal_service.py) — the same
# dual-permission requirement the original upload route itself uses
# (web/routers/check_images.py::_require_bulk_check_image_permissions), on top of the
# single permission above that already gates viewing/downloading the file.
_EXTRA_BACKOUT_PERMISSION_BY_KIND = {
    BulkUploadKind.CHECK_IMAGES: "paid_item:write",
}


def _get_authorized_record(db: Session, ctx: TenantContext, upload_id: uuid.UUID) -> BulkUploadFile:
    record = bulk_upload_file_service.get_uploaded_file(db, ctx.tenant_id, upload_id, ctx.customer_id)
    if record is None:
        raise WebNotFound()
    if _PERMISSION_BY_KIND[record.kind] not in ctx.permissions:
        raise WebForbidden()
    return record


@router.get("/{upload_id}")
def bulk_upload_detail(
    request: Request, upload_id: uuid.UUID, db: Session = Depends(get_db), ctx: TenantContext = Depends(get_web_context)
) -> HTMLResponse:
    record = _get_authorized_record(db, ctx, upload_id)
    # Recomputed live on every render — a stale "verified" flag stored at upload time
    # would defeat the entire point, which is detecting a change made SINCE then.
    verified = bulk_upload_file_service.verify_uploaded_file(db, ctx.tenant_id, upload_id, ctx.customer_id)
    tracked_records = bulk_upload_reversal_service.list_created_records(db, ctx.tenant_id, upload_id)
    return render_template(
        request, "bulk_uploads/detail.html", ctx=ctx, record=record, verified=verified, tracked_records=tracked_records
    )


@router.post("/{upload_id}/back-out")
def bulk_upload_back_out(
    request: Request,
    upload_id: uuid.UUID,
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(get_web_context),
    _csrf: None = Depends(verify_csrf),
) -> Response:
    record = _get_authorized_record(db, ctx, upload_id)
    extra_permission = _EXTRA_BACKOUT_PERMISSION_BY_KIND.get(record.kind)
    if extra_permission is not None and extra_permission not in ctx.permissions:
        raise WebForbidden()

    results = bulk_upload_reversal_service.back_out_upload(
        db, ctx.tenant_id, upload_id, actor_user_id=ctx.user_id, channel="web", scoped_customer_id=ctx.customer_id
    )
    if results is None:
        db.rollback()
        return RedirectResponse(
            f"/ui/bulk-uploads/{upload_id}?error={quote('Nothing to back out — this upload has already been backed out, or nothing it created was tracked.')}",
            status_code=303,
        )
    return render_template(
        request,
        "bulk_result.html",
        ctx=ctx,
        results=results,
        upload_record=record,
        back_url=f"/ui/bulk-uploads/{upload_id}",
        back_label="Back to upload details",
    )


@router.get("/{upload_id}/download")
def bulk_upload_download(
    upload_id: uuid.UUID, db: Session = Depends(get_db), ctx: TenantContext = Depends(get_web_context)
) -> Response:
    record = _get_authorized_record(db, ctx, upload_id)
    data = read_uploaded_file(record.storage_path)
    encoded_name = quote(record.original_filename)
    # Always a generic binary download, never the Content-Type the uploader's browser
    # declared: that value is attacker-controlled, and echoing e.g. text/javascript back
    # from this origin would let a <script src> elsewhere on the site load it.
    return Response(
        content=data,
        media_type="application/octet-stream",
        headers={"Content-Disposition": f"attachment; filename=\"upload\"; filename*=UTF-8''{encoded_name}"},
    )
