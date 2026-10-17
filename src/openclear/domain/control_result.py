# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from openclear.db.base import Base, new_uuid


class ControlTestResult(Base):
    """One run of one control check for a bank (CONTROLS_PLAN.md CT2; compliance/checks.py): its
    status, what it found and the period it covers, kept so a bank can show how a control did
    over a quarter. Every scheduled run is kept; a page view is kept only when the status
    changed. `evidence_sha256` is over the evidence as compact, key-sorted JSON."""

    __tablename__ = "control_test_result"
    __table_args__ = (Index("ix_control_test_result_tenant_check_recorded", "tenant_id", "check_key", "recorded_at"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id"), nullable=False, index=True)
    check_key: Mapped[str] = mapped_column(String(60), nullable=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False)  # PASS, WARN, FAIL
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    evidence: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    evidence_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    # A population check's period; None for a state check (the system as it was then).
    period_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    started_by: Mapped[str] = mapped_column(String(10), nullable=False)  # schedule, view, person
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("user.id"), nullable=True)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class ControlDeviation(Base):
    """A stretch of time a check was at FAIL (or WARN, for checks the bank marks so), from the
    result that opened it to the one that closed it, with the cause and the bank's response
    (CONTROLS_PLAN.md CT2)."""

    __tablename__ = "control_deviation"
    __table_args__ = (Index("ix_control_deviation_tenant_check_opened", "tenant_id", "check_key", "opened_at"),
                      Index("ix_control_deviation_tenant_id_updated_at", "tenant_id", "updated_at"))

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id"), nullable=False, index=True)
    check_key: Mapped[str] = mapped_column(String(60), nullable=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False)  # the worst it reached: WARN or FAIL
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    opened_result_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("control_test_result.id"), nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_result_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("control_test_result.id"), nullable=True)
    cause: Mapped[str | None] = mapped_column(Text, nullable=True)
    response: Mapped[str | None] = mapped_column(Text, nullable=True)
    responded_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("user.id"), nullable=True)
    responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Changes when it closes or is responded to: a GRC tool syncs from it (CT5).
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
