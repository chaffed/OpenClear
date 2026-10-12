# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import uuid
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column

from openclear.db.base import Base, new_uuid


class ImportLayout(Base):
    """How to read a customer's own issued-check file (UX_PLAN.md U1; bulk_import/layouts.py):
    delimited, fixed-width or Excel, which column or positions hold each field, and the
    amount and date formats. An upload that uses one keeps a copy of its settings in the
    upload's options, so a later edit never changes what an old upload meant.

    `customer_id` null is a bank-wide layout any of its customers can use; otherwise it's
    that customer's own. One default per bank or customer, and kind."""

    __tablename__ = "import_layout"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id"), nullable=False, index=True)
    customer_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("customer.id"), nullable=True, index=True)
    kind: Mapped[str] = mapped_column(String(30), nullable=False, default="issued_items")
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    settings: Mapped[dict] = mapped_column(JSON, nullable=False)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("user.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, onupdate=func.now())
