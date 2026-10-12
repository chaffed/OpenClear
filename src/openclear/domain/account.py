# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from openclear.db.base import Base, new_uuid


class AchDebitBlockMode(str, enum.Enum):
    # A genuine kill-switch: every ACH debit is rejected, even one that would otherwise
    # match an active authorization rule. Mirrors real "ACH debit block" products — an
    # account that expects zero ACH debits ever, full stop.
    BLOCK_ALL = "block_all"
    # The normal positive-pay posture ("ACH debit filter"): debits are evaluated against
    # authorization rules (originator whitelist, amount/frequency/SEC-code limits).
    FILTER_BY_AUTHORIZATION = "filter_by_authorization"


class Account(Base):
    __tablename__ = "account"
    __table_args__ = (
        UniqueConstraint("tenant_id", "account_number", name="uq_account_tenant_number"),
        UniqueConstraint("tenant_id", "external_account_id", name="uq_account_tenant_external_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    # When the row last changed: what list endpoints' updated_since filters on, so a bank's
    # data pipeline fetches only what changed (docs/guides/bi.md).
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id"), nullable=False, index=True)
    # Nullable: an account with no customer is a tenant-wide "house" account, visible
    # only to bank-wide (non-customer-scoped) staff — see TenantMembership.customer_id.
    customer_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("customer.id"), nullable=True, index=True)
    account_number: Mapped[str] = mapped_column(String(64), nullable=False)
    # A customer's own internal reference for this account, optional — bulk file imports
    # (services/account_service.py::get_account_by_number) accept this as a fallback
    # match whenever a row's account column doesn't match a real account_number, so a
    # customer never has to learn OpenClear's own numbering just to submit a file. Same
    # per-tenant uniqueness scope as account_number itself (NULL rows never collide).
    external_account_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    ach_debit_block_mode: Mapped[AchDebitBlockMode] = mapped_column(
        Enum(AchDebitBlockMode, name="ach_debit_block_mode", native_enum=False, length=32),
        nullable=False,
        default=AchDebitBlockMode.FILTER_BY_AUTHORIZATION,
    )
