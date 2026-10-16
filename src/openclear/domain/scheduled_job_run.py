# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from openclear.db.base import Base


class ScheduledJobRun(Base):
    """Each scheduled job's last run on this installation (workers/leader_lock.py), for the
    scheduled_jobs control check (CONTROLS_PLAN.md CT2). One row per job, for the whole
    installation: no bank's data."""

    __tablename__ = "scheduled_job_run"

    job_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    last_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_ok: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
