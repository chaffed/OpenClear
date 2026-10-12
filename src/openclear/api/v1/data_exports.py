# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""The full data export through the API (docs/guides/bi.md): every record of the bank, or of
one customer, as JSON files in a zip, built in the background. The same export as Settings →
Data export and a customer's own Data export page, for a data pipeline's first load or an
archive. A customer-scoped client exports its own customer; a bank-wide client the whole
bank, or one customer with `customer_number`."""

import uuid
from datetime import datetime
from urllib.parse import quote

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from openclear import storage
from openclear.api.v1.files import ZIP_FILE
from openclear.auth.deps import require_permission
from openclear.db.session import get_db
from openclear.db.tenancy import TenantContext
from openclear.domain.data_export_job import DataExportJob, DataExportJobStatus
from openclear.services import audit_log_service, customer_service, data_export_service
from openclear.workers import jobs
from openclear.workers.job_handlers import RUN_DATA_EXPORT

router = APIRouter(prefix="/data-exports", tags=["data exports"])
_run = require_permission("data_export:run")


class DataExportRead(BaseModel):
    id: uuid.UUID
    status: str
    customer_id: uuid.UUID | None
    requested_at: datetime | None
    completed_at: datetime | None
    size_bytes: int | None
    error: str | None
    file_url: str | None


def _read(job: DataExportJob) -> DataExportRead:
    done = job.status == DataExportJobStatus.COMPLETED and bool(job.archive_path)
    return DataExportRead(id=job.id, status=job.status.value, customer_id=job.customer_id, requested_at=job.requested_at,
                          completed_at=job.completed_at, size_bytes=job.size_bytes, error=job.error_message,
                          file_url=f"/api/v1/data-exports/{job.id}/file" if done else None)


def _scope(db: Session, ctx: TenantContext, customer_number: str | None) -> uuid.UUID | None:
    if ctx.customer_id is not None:
        return ctx.customer_id  # a customer's client only ever exports its own customer
    if not customer_number:
        return None
    customer = customer_service.get_customer_by_number(db, ctx.tenant_id, customer_number)
    if customer is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No customer with that number")
    return customer.id


def _job(db: Session, ctx: TenantContext, export_id: uuid.UUID) -> DataExportJob:
    job = db.get(DataExportJob, export_id)
    if job is None or job.tenant_id != ctx.tenant_id or (ctx.customer_id is not None and job.customer_id != ctx.customer_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Export not found")
    return job


@router.post("", response_model=DataExportRead, status_code=status.HTTP_202_ACCEPTED)
def start_export(
    background_tasks: BackgroundTasks,
    customer_number: str | None = Query(None, description="A bank-wide client: export only this customer"),
    db: Session = Depends(get_db), ctx: TenantContext = Depends(_run),
) -> DataExportRead:
    """Starts an export in the background. Poll `GET /api/v1/data-exports/{id}` until `status`
    is `completed`, then download `file_url`."""
    customer_id = _scope(db, ctx, customer_number)
    job = data_export_service.request_export(db, ctx.tenant_id, ctx.user_id, customer_id=customer_id)
    audit_log_service.record_action(
        db, ctx.tenant_id, actor_user_id=ctx.user_id, channel="api", action="data_export.request",
        summary="Requested a full data export" if customer_id is None else "Requested a customer data export",
        resource_type="data_export_job", resource_id=job.id,
    )
    jobs.enqueue(db, RUN_DATA_EXPORT, tenant_id=ctx.tenant_id, payload={"export_job_id": str(job.id)})
    db.commit()
    jobs.jobs_queued(db.get_bind(), background_tasks)
    return _read(job)


@router.get("", response_model=list[DataExportRead])
def list_exports(db: Session = Depends(get_db), ctx: TenantContext = Depends(_run)) -> list[DataExportRead]:
    """This client's scope's exports, newest first."""
    stmt = select(DataExportJob).where(DataExportJob.tenant_id == ctx.tenant_id)
    if ctx.customer_id is not None:
        stmt = stmt.where(DataExportJob.customer_id == ctx.customer_id)
    return [_read(j) for j in db.execute(stmt.order_by(DataExportJob.requested_at.desc()).limit(100)).scalars()]


@router.get("/{export_id}", response_model=DataExportRead)
def get_export(export_id: uuid.UUID, db: Session = Depends(get_db), ctx: TenantContext = Depends(_run)) -> DataExportRead:
    return _read(_job(db, ctx, export_id))


@router.get("/{export_id}/file", **ZIP_FILE)
def download_export(export_id: uuid.UUID, db: Session = Depends(get_db), ctx: TenantContext = Depends(_run)):
    """The archive: data/*.json, one file per kind of record, plus the files (check images,
    uploads) the records point to."""
    job = _job(db, ctx, export_id)
    if job.status != DataExportJobStatus.COMPLETED or not job.archive_path:
        raise HTTPException(status.HTTP_409_CONFLICT, f"The export is {job.status.value}, not ready to download")
    audit_log_service.record_action(db, ctx.tenant_id, actor_user_id=ctx.user_id, channel="api", action="data_export.download",
                                    summary="Downloaded a data export archive", resource_type="data_export_job", resource_id=job.id)
    db.commit()
    filename = f"openclear-export-{job.id}.zip"
    return StreamingResponse(storage.iter_chunks(job.archive_path), media_type="application/zip",
                             headers={"Content-Disposition": f"attachment; filename=\"export\"; filename*=UTF-8''{quote(filename)}"})
