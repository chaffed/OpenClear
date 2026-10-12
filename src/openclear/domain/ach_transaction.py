# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import enum
import uuid
from datetime import datetime
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Date, DateTime, Enum, ForeignKey, Index, Numeric, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from openclear.db.base import Base, new_uuid


class AchTransactionType(str, enum.Enum):
    DEBIT = "debit"
    CREDIT = "credit"


class AchMatchStatus(str, enum.Enum):
    PENDING = "pending"
    MATCHED = "matched"
    EXCEPTION = "exception"


class AchSettlementStatus(str, enum.Enum):
    PENDING = "pending"
    PAID = "paid"
    RETURNED = "returned"


class AchTransactionSource(str, enum.Enum):
    API = "api"
    BULK_FILE = "bulk_file"
    MANUAL = "manual"
    # A synthetic ach_transaction created by services/fraud_training_service.py to carry a
    # known-fraud training label — never a real incoming ACH entry.
    TRAINING_BACKFILL = "training_backfill"


class AchTransaction(Base):
    """An incoming ACH debit/credit — the 'source item' pointed to by exception_item
    (network_code='ach') when it fails the authorization-rule matching engine.

    `originator_id` = NACHA batch header "Company Identification". `receiver_id` = NACHA
    entry detail record "Individual Identification Number" (optional — not every SEC code
    populates it)."""

    __tablename__ = "ach_transaction"
    __table_args__ = (
        Index("ix_ach_transaction_tenant_id_updated_at", "tenant_id", "updated_at"),
        UniqueConstraint("tenant_id", "trace_number", name="uq_ach_transaction_tenant_trace"),
        # The ACH frequency-limit count (networks/ach/adapter.py) runs for every debit.
        Index("ix_ach_transaction_tenant_account_originator_date", "tenant_id", "account_id", "originator_id", "effective_date"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id"), nullable=False, index=True)
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("account.id"), nullable=False, index=True)
    # Denormalized from account.customer_id at creation time — see issued_item.py's
    # customer_id for the full rationale (same pattern on every customer-scoped table).
    customer_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("customer.id"), nullable=True, index=True)

    originator_id: Mapped[str] = mapped_column(String(32), nullable=False)
    originator_name: Mapped[str] = mapped_column(String(255), nullable=False)
    receiver_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    transaction_type: Mapped[AchTransactionType] = mapped_column(
        Enum(AchTransactionType, name="ach_transaction_type", native_enum=False, length=10), nullable=False
    )
    sec_code: Mapped[str] = mapped_column(String(3), nullable=False)
    trace_number: Mapped[str] = mapped_column(String(32), nullable=False)
    effective_date: Mapped[date] = mapped_column(Date, nullable=False)

    match_status: Mapped[AchMatchStatus] = mapped_column(
        Enum(AchMatchStatus, name="ach_match_status", native_enum=False, length=20),
        nullable=False,
        default=AchMatchStatus.PENDING,
    )
    settlement_status: Mapped[AchSettlementStatus] = mapped_column(
        Enum(AchSettlementStatus, name="ach_settlement_status", native_enum=False, length=20),
        nullable=False,
        default=AchSettlementStatus.PENDING,
    )
    source: Mapped[AchTransactionSource] = mapped_column(
        Enum(AchTransactionSource, name="ach_transaction_source", native_enum=False, length=20), nullable=False
    )

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # When the row last changed: what list endpoints' updated_since filters on, so a bank's
    # data pipeline fetches only what changed (docs/guides/bi.md).
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
