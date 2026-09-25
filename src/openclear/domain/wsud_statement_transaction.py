# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from openclear.db.base import Base, new_uuid


class WsudStatementTransaction(Base):
    """Many-to-many link between one signed WsudStatement and the ACH transactions it
    covers — a single statement can attest to several disputed items at once. Unique on
    the pair so the same transaction can't be attached to one statement twice, though a
    transaction may legitimately be covered by more than one statement over time (e.g.
    signed again after a dispute is reopened) — see
    services/wsud_service.py::list_wsud_eligible_transactions, which shows rather than
    hides already-covered transactions for exactly this reason."""

    __tablename__ = "wsud_statement_transaction"
    __table_args__ = (UniqueConstraint("wsud_statement_id", "ach_transaction_id", name="uq_wsud_statement_transaction"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    # Denormalized here too (not just derivable via a join to wsud_statement), same
    # "every tenant-owned table carries its own tenant_id" convention as
    # BulkUploadCreatedRecord — needed for the RLS policy on this table.
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id"), nullable=False, index=True)
    wsud_statement_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("wsud_statement.id"), nullable=False, index=True)
    ach_transaction_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("ach_transaction.id"), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
