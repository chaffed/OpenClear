# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import enum
import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Index, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from openclear.db.base import Base, new_uuid


class ChangeRequestStatus(str, enum.Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    WITHDRAWN = "withdrawn"
    EXPIRED = "expired"
    APPLIED_ALONE = "applied_alone"  # nobody else could approve: applied after the cooling-off (DUAL_CONTROL_PLAN.md DC-D6)


class ChangeRequest(Base):
    """A protected change waiting for a second person (DUAL_CONTROL_PLAN.md;
    services/change_request_service.py): what it changes, the value before and after, who
    asked and when it expires. Whoever asks can't approve it. `scope` is the service provider's
    (tenant_id null), a bank's, or a customer's (customer_id set)."""

    __tablename__ = "change_request"
    __table_args__ = (Index("ix_change_request_tenant_status", "tenant_id", "status"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tenant.id"), nullable=True, index=True)
    customer_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("customer.id"), nullable=True)
    scope: Mapped[str] = mapped_column(String(10), nullable=False, default="bank", server_default="bank")  # provider, bank, customer
    kind: Mapped[str] = mapped_column(String(60), nullable=False)  # services/change_kinds.py
    setting: Mapped[str | None] = mapped_column(String(160), nullable=True)  # one open request per setting
    summary: Mapped[str] = mapped_column(String(500), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)  # what applying it needs
    before: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # the value when asked, to show and to check it's still current
    after: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)  # the requester's, if given
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=ChangeRequestStatus.PENDING.value)
    requested_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("user.id"), nullable=True)  # None: a platform API key
    requested_with: Mapped[str | None] = mapped_column(String(60), nullable=True)  # web, api, or a platform API key's id
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    applies_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)  # a cooling-off request's
    decided_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("user.id"), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decided_reason: Mapped[str | None] = mapped_column(Text, nullable=True)  # a rejection's, required
