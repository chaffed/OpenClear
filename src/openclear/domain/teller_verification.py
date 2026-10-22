# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import JSON, DateTime, ForeignKey, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column

from openclear.db.base import Base, new_uuid


class TellerVerification(Base):
    """A teller platform asking whether a check can be cashed at a branch (TELLER_PLAN.md TL-1),
    and, once the cash is paid, the record of it (TL-2). `result` is "match" or the first
    problem; `problems` lists every one. A match can be cashed once, within 15 minutes."""

    __tablename__ = "teller_verification"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id"), nullable=False, index=True)
    # Null when the account isn't one OpenClear knows: "not_on_positive_pay".
    account_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("account.id"), nullable=True, index=True)
    customer_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("customer.id"), nullable=True, index=True)
    routing_number: Mapped[str | None] = mapped_column(String(9), nullable=True)
    account_number: Mapped[str] = mapped_column(String(64), nullable=False)
    check_number: Mapped[str] = mapped_column(String(32), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    payee_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    result: Mapped[str] = mapped_column(String(30), nullable=False)
    problems: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    issued_item_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("issued_item.id"), nullable=True)
    # Who asked, as the teller platform says (it authenticates its tellers, OpenClear doesn't).
    teller_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    branch: Mapped[str | None] = mapped_column(String(64), nullable=True)
    api_client_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    cashed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    paid_item_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("paid_item.id"), nullable=True)
