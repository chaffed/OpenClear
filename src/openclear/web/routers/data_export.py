# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import uuid
from urllib.parse import quote

from fastapi import APIRouter, BackgroundTasks, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response, StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from openclear.db.session import get_db
from openclear.db.tenancy import TenantContext
from openclear.domain.data_export_job import DataExportJob, DataExportJobStatus
from openclear import storage
from openclear.config import get_settings
from openclear.services import audit_log_service, customer_service, data_export_service
from openclear.web.deps import WebForbidden, WebNotFound, render_template, require_web_permission
from openclear.web.redirects import see_other
from openclear.web.security import verify_csrf
from openclear.workers import jobs
from openclear.workers.job_handlers import RUN_DATA_EXPORT

router = APIRouter(tags=["web-data-export"])


def _list_jobs(db: Session, tenant_id: uuid.UUID, customer_id: uuid.UUID | None) -> list[DataExportJob]:
    stmt = (
        select(DataExportJob)
        .where(DataExportJob.tenant_id == tenant_id, DataExportJob.customer_id == customer_id)
        .order_by(DataExportJob.requested_at.desc())
    )
    return list(db.execute(stmt).scalars().all())


def _get_job(db: Session, tenant_id: uuid.UUID, customer_id: uuid.UUID | None, job_id: uuid.UUID) -> DataExportJob | None:
    stmt = select(DataExportJob).where(
        DataExportJob.id == job_id, DataExportJob.tenant_id == tenant_id, DataExportJob.customer_id == customer_id
    )
    return db.execute(stmt).scalar_one_or_none()


def _start_job(
    db: Session, background_tasks: BackgroundTasks, ctx: TenantContext, customer_id: uuid.UUID | None
) -> DataExportJob:
    job = data_export_service.request_export(db, ctx.tenant_id, ctx.user_id, customer_id=customer_id)
    audit_log_service.record_action(
        db,
        ctx.tenant_id,
        actor_user_id=ctx.user_id,
        channel="web",
        action="data_export.request",
        summary="Requested a full data export" if customer_id is None else "Requested a customer data export",
        resource_type="data_export_job",
        resource_id=job.id,
    )
    jobs.enqueue(db, RUN_DATA_EXPORT, tenant_id=ctx.tenant_id, payload={"export_job_id": str(job.id)})
    db.commit()
    jobs.jobs_queued(db.get_bind(), background_tasks)
    return job


def _download_response(db: Session, ctx: TenantContext, job: DataExportJob) -> Response:
    audit_log_service.record_action(
        db,
        ctx.tenant_id,
        actor_user_id=ctx.user_id,
        channel="web",
        action="data_export.download",
        summary="Downloaded a data export archive",
        resource_type="data_export_job",
        resource_id=job.id,
    )
    db.commit()
    filename = f"openclear-export-{job.id}.zip"
    return StreamingResponse(
        storage.iter_chunks(job.archive_path),
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename=\"export\"; filename*=UTF-8''{quote(filename)}"},
    )


# --- Bank-wide: /ui/settings/data-export/* ---
#
# data_export:run is deliberately NOT in CUSTOMER_SCOPE_MASKED_PERMISSIONS — a customer's
# own users may export their own customer's data (per-customer routes below). So these
# routes can't rely on the permission alone: a customer-scoped session must never reach
# the tenant-wide export, and must only ever reach its own customer's per-customer one.


def _require_bank_wide_session(ctx: TenantContext) -> None:
    if ctx.customer_id is not None:
        raise WebForbidden()


@router.get("/ui/settings/data-export")
def list_bank_exports(
    request: Request, db: Session = Depends(get_db), ctx: TenantContext = Depends(require_web_permission("data_export:run"))
) -> HTMLResponse:
    _require_bank_wide_session(ctx)
    jobs = _list_jobs(db, ctx.tenant_id, None)
    return render_template(request, "settings/data_export.html", ctx=ctx, jobs=jobs, retention_days=get_settings().retention_data_exports_days)


@router.post("/ui/settings/data-export/start")
def start_bank_export(
    background_tasks: BackgroundTasks,
    confirm: bool = Form(False),
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("data_export:run")),
    _csrf: None = Depends(verify_csrf),
) -> RedirectResponse:
    _require_bank_wide_session(ctx)
    if not confirm:
        return see_other("/ui/settings/data-export", error="Please check the confirmation box to start an export.")
    _start_job(db, background_tasks, ctx, customer_id=None)
    return see_other("/ui/settings/data-export", flash="Export started.")


@router.get("/ui/settings/data-export/{job_id}/download")
def download_bank_export(
    job_id: uuid.UUID, db: Session = Depends(get_db), ctx: TenantContext = Depends(require_web_permission("data_export:run"))
) -> Response:
    _require_bank_wide_session(ctx)
    job = _get_job(db, ctx.tenant_id, None, job_id)
    if job is None or job.status != DataExportJobStatus.COMPLETED or not job.archive_path:
        raise WebNotFound()
    return _download_response(db, ctx, job)


# --- Per-customer: /ui/customers/{customer_id}/data-export/* ---


def _get_customer_or_404(db: Session, ctx: TenantContext, customer_id: uuid.UUID):
    # A customer-scoped session may only reach its own customer — 404 rather than 403
    # for any other, so this never reveals whether that other customer exists.
    if ctx.customer_id is not None and ctx.customer_id != customer_id:
        raise WebNotFound()
    customer = customer_service.get_customer(db, ctx.tenant_id, customer_id)
    if customer is None:
        raise WebNotFound()
    return customer


@router.get("/ui/customers/{customer_id}/data-export")
def list_customer_exports(
    request: Request, customer_id: uuid.UUID, db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("data_export:run")),
) -> HTMLResponse:
    customer = _get_customer_or_404(db, ctx, customer_id)
    jobs = _list_jobs(db, ctx.tenant_id, customer_id)
    return render_template(
        request, "customers/data_export.html", ctx=ctx, customer=customer, jobs=jobs, retention_days=get_settings().retention_data_exports_days
    )


@router.post("/ui/customers/{customer_id}/data-export/start")
def start_customer_export(
    customer_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    confirm: bool = Form(False),
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("data_export:run")),
    _csrf: None = Depends(verify_csrf),
) -> RedirectResponse:
    _get_customer_or_404(db, ctx, customer_id)
    if not confirm:
        return see_other(f"/ui/customers/{customer_id}/data-export", error="Please check the confirmation box to start an export.")
    _start_job(db, background_tasks, ctx, customer_id=customer_id)
    return see_other(f"/ui/customers/{customer_id}/data-export", flash="Export started.")


@router.get("/ui/customers/{customer_id}/data-export/{job_id}/download")
def download_customer_export(
    customer_id: uuid.UUID, job_id: uuid.UUID, db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("data_export:run")),
) -> Response:
    _get_customer_or_404(db, ctx, customer_id)
    job = _get_job(db, ctx.tenant_id, customer_id, job_id)
    if job is None or job.status != DataExportJobStatus.COMPLETED or not job.archive_path:
        raise WebNotFound()
    return _download_response(db, ctx, job)
