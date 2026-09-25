# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import enum
import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Date, DateTime, Enum, ForeignKey, Numeric, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from openclear.db.base import Base, new_uuid


class IssuedItemStatus(str, enum.Enum):
    OUTSTANDING = "outstanding"
    PAID = "paid"
    VOIDED = "voided"
    STOPPED = "stopped"
    STALE = "stale"


class IssuedItemSource(str, enum.Enum):
    API = "api"
    BULK_FILE = "bulk_file"
    MANUAL = "manual"


class IssuedItem(Base):
    __tablename__ = "issued_item"
    __table_args__ = (
        UniqueConstraint("tenant_id", "account_id", "check_number", name="uq_issued_item_tenant_account_check"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id"), nullable=False, index=True)
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("account.id"), nullable=False, index=True)
    # Denormalized from account.customer_id at creation time (never trusted from a
    # request) — same pattern as tenant_id, so customer-scoped queries stay a flat
    # WHERE clause instead of a join. See CustomerScopedRepository.
    customer_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("customer.id"), nullable=True, index=True)

    check_number: Mapped[str] = mapped_column(String(32), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    payee_name: Mapped[str] = mapped_column(String(255), nullable=False)
    issue_date: Mapped[date] = mapped_column(Date, nullable=False)

    status: Mapped[IssuedItemStatus] = mapped_column(
        Enum(IssuedItemStatus, name="issued_item_status", native_enum=False, length=20),
        nullable=False,
        default=IssuedItemStatus.OUTSTANDING,
    )
    source: Mapped[IssuedItemSource] = mapped_column(
        Enum(IssuedItemSource, name="issued_item_source", native_enum=False, length=20), nullable=False
    )
    void_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    voided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    submitted_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("user.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
