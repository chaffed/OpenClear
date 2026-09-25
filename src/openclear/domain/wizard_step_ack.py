# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from openclear.db.base import Base, new_uuid


class WizardStepAck(Base):
    """Persists a manual "mark as done" for one implementation-wizard step —
    services/wizard_service.py. Only exists for steps with no single queryable
    completion signal (e.g. "review your security groups" — keeping the seeded defaults
    is a legitimate, deliberate choice, not an incomplete state); steps with a real
    queryable fact (e.g. "add an account") are checked live against the data instead and
    never need a row here. `customer_id` is None for a bank-wizard step, set for one
    customer's own wizard step — same NULL-means-bank-wide shape used by every other
    per-customer table this app has (MlModel, SsoConnection, DataExportJob, ...)."""

    __tablename__ = "wizard_step_ack"
    __table_args__ = (UniqueConstraint("tenant_id", "customer_id", "step_key", name="uq_wizard_step_ack_scope_step"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id"), nullable=False, index=True)
    customer_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("customer.id"), nullable=True, index=True)
    step_key: Mapped[str] = mapped_column(String(100), nullable=False)

    acknowledged_by_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("user.id"), nullable=False)
    acknowledged_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
