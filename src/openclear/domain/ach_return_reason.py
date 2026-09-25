# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column

from openclear.db.base import Base, new_uuid


class AchReturnReason(Base):
    """A tenant-defined catalog entry for returning an ACH exception — see
    services/decision_service.py::decide(), which requires selecting one of these
    (rather than freeform text) whenever an ACH exception is finalized as RETURN.
    `transaction_code` is *not* a NACHA return-reason code (R01 etc.) — it's the bank's
    own core banking system's posting code for the reversal (often 2-3 digits), which
    OpenClear only records and displays; it never transmits or executes anything against a
    core system. Never hard-deleted (`is_active` toggle instead), consistent with this
    app's philosophy elsewhere, since a historical Decision may still reference a
    since-retired reason's text/code."""

    __tablename__ = "ach_return_reason"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id"), nullable=False, index=True)
    reason_text: Mapped[str] = mapped_column(String(255), nullable=False)
    transaction_code: Mapped[str | None] = mapped_column(String(10), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
