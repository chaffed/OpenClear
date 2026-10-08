# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Billing for positive pay services (BILLING_PLAN.md): a bank's fee schedule, how each
customer is billed, and per-customer pricing terms. Statements are calculated from these
plus the activity in a period (services/billing/)."""

import enum
import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import JSON, Boolean, Date, DateTime, Enum, ForeignKey, Integer, Numeric, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from openclear.db.base import Base, new_uuid


class BillingMethod(str, enum.Enum):
    """How a customer (or the bank's own unassigned accounts) pays for services. The
    camt.086 payment method each line is reported with follows from this."""

    # Account analysis: charges are reported to the bank's analysis system, which offsets
    # them with earnings credit on the customer's balances (camt.086 BCMP).
    ANALYSIS = "analysis"
    # Hard charge: billed directly, typically debited from the billing account (FLAT).
    HARD_CHARGE = "hard_charge"
    # Not billed. Charges are still calculated and reported as waived (WVED).
    EXEMPT = "exempt"


class FeeItem(Base):
    """One line in a bank's fee schedule: what's counted (`metric`, see
    services/billing/metrics.py), how it's priced, and how it's coded on statements.

    `afp_code` is the bank's own AFP Service Code for the service; banks map positive pay
    to different codes, so none is assumed. `tiers`, when set, is step-tiered volume
    pricing: a list of {"up_to": int | null, "unit_price": "0.1000"}, in order, the last
    with up_to null; each tier's units are charged at that tier's price."""

    __tablename__ = "billing_fee_item"
    __table_args__ = (UniqueConstraint("tenant_id", "code", name="uq_billing_fee_item_tenant_code"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id"), nullable=False, index=True)
    code: Mapped[str] = mapped_column(String(40), nullable=False)
    description: Mapped[str] = mapped_column(String(200), nullable=False)
    metric: Mapped[str] = mapped_column(String(60), nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False, default=Decimal("0"))
    tiers: Mapped[list | None] = mapped_column(JSON, nullable=True)
    minimum_charge: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    afp_code: Mapped[str | None] = mapped_column(String(16), nullable=True)
    bank_service_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    taxable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    effective_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, onupdate=func.now())


class CustomerBillingTerms(Base):
    """How one customer is billed. No row means the bank's default method
    (Tenant.customer_default_billing_method), the customer's first account as the billing
    account, and list prices."""

    __tablename__ = "customer_billing_terms"
    __table_args__ = (UniqueConstraint("customer_id", name="uq_customer_billing_terms_customer"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id"), nullable=False, index=True)
    customer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("customer.id"), nullable=False, index=True)
    billing_method: Mapped[BillingMethod] = mapped_column(
        Enum(BillingMethod, name="billing_method", native_enum=False, length=20), nullable=False
    )
    # The account analyzed, or debited for hard charges.
    billing_account_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("account.id"), nullable=True)
    discount_percent: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False, default=Decimal("0"))
    # Waive every charge (e.g. a promotional period); lines are reported as waived (WVED).
    waive_all: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    notes: Mapped[str | None] = mapped_column(String(500), nullable=True)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class CustomerFeeOverride(Base):
    """A negotiated price for one fee for one customer, or that fee waived. A set
    `unit_price` replaces the fee's list price (and its tiers)."""

    __tablename__ = "customer_fee_override"
    __table_args__ = (UniqueConstraint("customer_id", "fee_item_id", name="uq_customer_fee_override"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id"), nullable=False, index=True)
    customer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("customer.id"), nullable=False, index=True)
    fee_item_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("billing_fee_item.id", ondelete="CASCADE"), nullable=False)
    unit_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 4), nullable=True)
    waived: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
