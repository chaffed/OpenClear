# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import uuid
from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from openclear.db.base import Base, new_uuid


class BankClosure(Base):
    """A day the bank is closed beyond weekends and the Federal Reserve's holidays: no
    decision cut-off falls on it (services/cutoff_service.py, UI_PLAN.md UI-D13)."""

    __tablename__ = "bank_closure"
    __table_args__ = (UniqueConstraint("tenant_id", "closed_on", name="uq_bank_closure_tenant_day"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id"), nullable=False, index=True)
    closed_on: Mapped[date] = mapped_column(Date, nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
