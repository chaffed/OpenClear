# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Background jobs, for the platform operator (a platform API key with the `operations`
scope): what's queued, running, or has failed, and a way to retry a failed one. A job
gives up after `job_max_attempts`; it then stays FAILED, with its last error, until
someone looks at it here (see workers/jobs.py)."""

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from openclear.api.v1.pagination import Page, page_params, set_page_headers
from openclear.auth.platform_api_key_deps import require_platform_scope
from openclear.db.session import get_db
from openclear.domain.job import Job, JobStatus
from openclear.domain.platform_api_key import PlatformApiKey
from openclear.workers.jobs import jobs_queued

router = APIRouter(prefix="/platform/jobs", tags=["platform-jobs"])
_operator = require_platform_scope("operations")


class JobRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    kind: str
    tenant_id: uuid.UUID | None
    status: JobStatus
    attempts: int
    max_attempts: int
    run_after: datetime
    created_at: datetime | None
    started_at: datetime | None
    finished_at: datetime | None
    last_error: str | None


@router.get("/summary")
def job_summary(db: Session = Depends(get_db), _key: PlatformApiKey = Depends(_operator)) -> dict[str, int]:
    """How many jobs are in each status, e.g. {"queued": 3, "running": 1, "failed": 0, ...}."""
    counts = dict(db.execute(select(Job.status, func.count()).group_by(Job.status)).all())
    return {s.value: counts.get(s, 0) for s in JobStatus}


@router.get("", response_model=list[JobRead])
def list_jobs(
    request: Request,
    response: Response,
    status_filter: JobStatus | None = None,
    kind: str | None = None,
    page: Page = Depends(page_params),
    db: Session = Depends(get_db),
    _key: PlatformApiKey = Depends(_operator),
) -> list[JobRead]:
    """Newest first. `status_filter=failed` is the one to watch."""
    stmt = select(Job)
    if status_filter is not None:
        stmt = stmt.where(Job.status == status_filter)
    if kind is not None:
        stmt = stmt.where(Job.kind == kind)
    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    rows = db.execute(stmt.order_by(Job.created_at.desc(), Job.id).offset(page.offset).limit(page.limit)).scalars().all()
    set_page_headers(request, response, page, total)
    return [JobRead.model_validate(j) for j in rows]


@router.post("/{job_id}/retry", response_model=JobRead)
def retry_job(job_id: uuid.UUID, db: Session = Depends(get_db), _key: PlatformApiKey = Depends(_operator)) -> JobRead:
    """Queues a FAILED job again with a fresh set of attempts (after fixing whatever made
    it fail). Any other status is a 409."""
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job not found")
    if job.status != JobStatus.FAILED:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Only a failed job can be retried; this one is {job.status.value}")
    job.status, job.attempts, job.run_after, job.finished_at = JobStatus.QUEUED, 0, datetime.now(timezone.utc), None
    db.commit()
    jobs_queued(db.get_bind())
    db.refresh(job)
    return JobRead.model_validate(job)
