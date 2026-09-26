# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import enum
import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Date, DateTime, Enum, ForeignKey, Index, Integer, JSON, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column

from openclear.db.base import Base, new_uuid


class AchAuthorizationStatus(str, enum.Enum):
    ACTIVE = "active"
    REVOKED = "revoked"


class AchAuthorizationRule(Base):
    """An account holder's whitelist entry for an ACH originator (company ID) — the ACH
    analogue of an issued_item, but a standing rule rather than a one-off instrument.

    `originator_id` corresponds to the NACHA batch header's "Company Identification"
    field (shared by every entry in a batch). `receiver_id` corresponds to the NACHA
    entry detail record's "Individual Identification Number" (e.g. an employee/member ID
    the originator assigns to identify who's being debited) — it's optional: a rule with
    `receiver_id=None` is a blanket authorization for the originator covering any
    receiver, while a rule with a specific `receiver_id` scopes down to just that one.
    When both exist for the same originator, the more specific (exact receiver_id) rule
    wins — see networks/ach/rules.py."""

    __tablename__ = "ach_authorization_rule"
    __table_args__ = (Index("ix_ach_authorization_rule_tenant_account_originator", "tenant_id", "account_id", "originator_id"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id"), nullable=False, index=True)
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("account.id"), nullable=False, index=True)
    # Denormalized from account.customer_id at creation time — see issued_item.py's
    # customer_id for the full rationale (same pattern on every customer-scoped table).
    customer_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("customer.id"), nullable=True, index=True)

    originator_id: Mapped[str] = mapped_column(String(32), nullable=False)
    originator_name: Mapped[str] = mapped_column(String(255), nullable=False)
    receiver_id: Mapped[str | None] = mapped_column(String(32), nullable=True)

    status: Mapped[AchAuthorizationStatus] = mapped_column(
        Enum(AchAuthorizationStatus, name="ach_authorization_status", native_enum=False, length=20),
        nullable=False,
        default=AchAuthorizationStatus.ACTIVE,
    )
    max_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    frequency_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)  # max debits per period (30d)
    allowed_sec_codes: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)

    effective_date: Mapped[date] = mapped_column(Date, nullable=False)
    expiration_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("user.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
