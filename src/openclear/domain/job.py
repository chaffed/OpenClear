# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import enum
import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, Enum, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from openclear.db.base import Base, new_uuid


class JobStatus(str, enum.Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"  # gave up after max_attempts; stays visible to the operator


class Job(Base):
    """One unit of background work (workers/jobs.py): OCR on an uploaded image, building
    a data export, one bank's dropbox scan, one model slot's retrain. Workers claim queued
    rows (FOR UPDATE SKIP LOCKED on Postgres), keep `heartbeat_at` fresh while they run,
    and a row whose heartbeat stops (its worker died) is queued again. A failure is
    retried with back-off up to `max_attempts`, then left FAILED with its error.

    Deliberately not under row-level security: workers pick up every bank's jobs, then
    bind their session to the job's bank (`tenant_id`) before running it."""

    __tablename__ = "job"
    __table_args__ = (
        # What a worker asks for: the oldest queued job that's due.
        Index("ix_job_status_run_after", "status", "run_after"),
        Index("ix_job_dedupe_key_status", "dedupe_key", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tenant.id"), nullable=True, index=True)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    # Enqueuing the same work twice while it's still waiting is a no-op (e.g. the
    # scheduler asking for bank X's dropbox scan before the last one has run).
    dedupe_key: Mapped[str | None] = mapped_column(String(200), nullable=True)

    status: Mapped[JobStatus] = mapped_column(
        Enum(JobStatus, name="job_status", native_enum=False, length=20), nullable=False, default=JobStatus.QUEUED
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    run_after: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    locked_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
