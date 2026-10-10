# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import uuid
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Index, String, func
from sqlalchemy.orm import Mapped, mapped_column

from openclear.db.base import Base, new_uuid


class ControlRun(Base):
    """One run of a control test (COMPLIANCE_PLAN.md C2): a historical file replayed through
    today's rules ("mrm_replay"), or the synthetic anomaly-capture test ("anomaly_capture").
    Both run in a dry run that's rolled back; this row and its report are what's kept, as
    evidence for model validation (SR 11-7) and for the Controls page."""

    __tablename__ = "control_run"
    __table_args__ = (Index("ix_control_run_tenant_kind_started", "tenant_id", "kind", "started_at"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id"), nullable=False)
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    passed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)  # None: a report with no pass/fail (a replay)
    summary: Mapped[str] = mapped_column(String(500), nullable=False)
    report: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    requested_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("user.id"), nullable=True)  # None: the daily schedule
