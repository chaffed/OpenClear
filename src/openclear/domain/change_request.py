# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import enum
import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column

from openclear.db.base import Base, new_uuid


class ChangeRequestStatus(str, enum.Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class ChangeRequest(Base):
    """A change to how items are judged (turning on a matching rule, promoting a scoring
    model) waiting for a second person, under segregation of duties in enforce mode
    (auth/sod.py; COMPLIANCE_PLAN.md C1). Whoever asks can't approve it."""

    __tablename__ = "change_request"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id"), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(60), nullable=False)  # "matching_rule.enable", "ml_model.activate"
    summary: Mapped[str] = mapped_column(String(500), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=ChangeRequestStatus.PENDING.value)
    requested_by_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("user.id"), nullable=False)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    decided_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("user.id"), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
