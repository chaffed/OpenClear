# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from openclear.db.base import Base, new_uuid


class BankDeletion(Base):
    """The deletion of a leaving bank's data, and its signed certificate (plans/OFFBOARDING_PLAN.md;
    services/offboarding_service.py). The service provider's record, so it has no tenant_id and
    no row-level security: the bank's own rows are what it deletes."""

    __tablename__ = "bank_deletion"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    bank_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="queued")  # queued, done, cancelled, refused, failed
    requested_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("user.id"), nullable=True)
    approved_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("user.id"), nullable=True)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    runs_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)  # after the delay, still cancellable
    # Deleting without a full export since offboarding started: why (the bank declined in writing, say).
    override_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    certificate: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # {"bundle": ..., "signature": ...}
    certificate_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)


class CustomerDeletion(Base):
    """The deletion of a customer's data after its last service day, with its signed certificate
    (plans/CUSTOMER_OFFBOARDING_PLAN.md). The bank's record: it outlives the customer's rows."""

    __tablename__ = "customer_deletion"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id"), nullable=False, index=True)
    customer_id: Mapped[uuid.UUID] = mapped_column(nullable=False)  # not a foreign key: the customer is gone
    customer_number: Mapped[str] = mapped_column(String(64), nullable=False)
    customer_name: Mapped[str] = mapped_column(String(255), nullable=False)
    service_ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    certificate: Mapped[dict] = mapped_column(JSON, nullable=False)  # {"bundle": ..., "signature": ...}
    certificate_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
