# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""The web side of a bulk upload imported in the background (SCALE_PLAN.md S4): store
the file, reject it on the form if it can't be parsed, otherwise queue the import and go
to the upload's page, which shows progress and then the results."""

from fastapi import Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.orm import Session

from openclear.db.tenancy import TenantContext
from openclear.domain.bulk_upload_file import BulkUploadKind, BulkUploadStatus
from openclear.services import bulk_import_service
from openclear.web.deps import render_template
from openclear.workers.jobs import jobs_queued


def start_import(
    request: Request,
    db: Session,
    ctx: TenantContext,
    upload_file: UploadFile,
    *,
    kind: BulkUploadKind,
    file_format: str,
    form_template: str,
    default_filename: str,
    error_field: str = "error",
    options: dict | None = None,
) -> Response | HTMLResponse:
    record = bulk_import_service.receive_upload(
        db,
        ctx.tenant_id,
        kind=kind,
        file_format=file_format,
        filename=upload_file.filename or default_filename,
        content_type=upload_file.content_type,
        uploaded_by_user_id=ctx.user_id,
        customer_id=ctx.customer_id,
        stream=upload_file.file,
        options=options,
    )
    if record.status == BulkUploadStatus.REJECTED:
        return render_template(request, form_template, ctx=ctx, status_code=422, upload_record=record, **{error_field: record.error})
    jobs_queued(db.get_bind())
    return RedirectResponse(f"/ui/bulk-uploads/{record.id}", status_code=303)
