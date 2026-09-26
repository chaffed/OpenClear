# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import enum
import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Date, DateTime, Enum, ForeignKey, Index, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column

from openclear.db.base import Base, new_uuid


class PaidItemMatchStatus(str, enum.Enum):
    PENDING = "pending"  # created, matching not yet run (should be transient)
    MATCHED = "matched"
    EXCEPTION = "exception"


class PaidItemSettlementStatus(str, enum.Enum):
    PENDING = "pending"
    PAID = "paid"
    RETURNED = "returned"
    # Set only by services/bulk_upload_reversal_service.py when the bulk upload that
    # created this paid_item is backed out — excluded from
    # CheckAdapter._is_duplicate_paid's "settlement_status == PAID" filter, so a
    # reversed item correctly stops counting as a prior legitimate payment.
    REVERSED = "reversed"


class PaidItemSource(str, enum.Enum):
    API = "api"
    BULK_FILE = "bulk_file"
    MANUAL = "manual"
    # A synthetic paid_item created by services/fraud_training_service.py to carry a
    # known-fraud training label — never a real presented check.
    TRAINING_BACKFILL = "training_backfill"


class PaidItem(Base):
    """A presented check clearing through the bank — the item positive pay matches
    against the issued-item file. This is the 'source item' pointed to by exception_item
    (network_code='check') when matching fails."""

    __tablename__ = "paid_item"
    # The duplicate-paid check runs for every presented check (networks/check/adapter.py).
    __table_args__ = (Index("ix_paid_item_tenant_account_check", "tenant_id", "account_id", "check_number"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id"), nullable=False, index=True)
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("account.id"), nullable=False, index=True)
    # Denormalized from account.customer_id at creation time — see issued_item.py's
    # customer_id for the full rationale (same pattern on every customer-scoped table).
    customer_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("customer.id"), nullable=True, index=True)

    check_number: Mapped[str] = mapped_column(String(32), nullable=False)
    presented_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    presented_date: Mapped[date] = mapped_column(Date, nullable=False)

    matched_issued_item_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("issued_item.id"), nullable=True)
    match_status: Mapped[PaidItemMatchStatus] = mapped_column(
        Enum(PaidItemMatchStatus, name="paid_item_match_status", native_enum=False, length=20),
        nullable=False,
        default=PaidItemMatchStatus.PENDING,
    )
    settlement_status: Mapped[PaidItemSettlementStatus] = mapped_column(
        Enum(PaidItemSettlementStatus, name="paid_item_settlement_status", native_enum=False, length=20),
        nullable=False,
        default=PaidItemSettlementStatus.PENDING,
    )
    source: Mapped[PaidItemSource] = mapped_column(
        Enum(PaidItemSource, name="paid_item_source", native_enum=False, length=20), nullable=False
    )

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
