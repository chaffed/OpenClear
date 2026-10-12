# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from openclear.db.base import Base, new_uuid


class DeadlineReminder(Base):
    """That an exception was included in the reminder sent `offset_minutes` before its
    decision deadline (UX_PLAN.md U3; services/reminder_service.py), so each reminder goes
    out once. One row per exception and offset; the messages themselves are notifications,
    one per person, covering every item due."""

    __tablename__ = "deadline_reminder"
    __table_args__ = (UniqueConstraint("exception_item_id", "offset_minutes"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id"), nullable=False, index=True)
    exception_item_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("exception_item.id"), nullable=False)
    offset_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
