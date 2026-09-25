# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from openclear.db.base import Base, new_uuid


class MlScoringMode(str, enum.Enum):
    # Use the customer's own active model once one exists for this network; otherwise
    # fall back to the global model. Default — this is what makes the switch from global
    # to customer-specific scoring happen on its own once enough data has accumulated.
    AUTO = "auto"
    # Always use the global model for this customer, even if a customer-specific one is
    # active — an admin's explicit "don't trust/use their model yet" override.
    GLOBAL = "global"
    # Pin to the customer-specific model; falls back to global if none is active yet.
    CUSTOMER = "customer"


class CustomerMlSetting(Base):
    """One row per (customer, network) a bank admin has ever touched the ML scoring mode
    for — absence of a row means AUTO (see ml/predict.py::_resolve_scoring_source), so
    this table only needs to grow when someone actually overrides the default."""

    __tablename__ = "customer_ml_setting"
    __table_args__ = (UniqueConstraint("customer_id", "network_code", name="uq_customer_ml_setting_customer_network"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id"), nullable=False, index=True)
    customer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("customer.id"), nullable=False, index=True)
    network_code: Mapped[str] = mapped_column(ForeignKey("payment_network.code"), nullable=False)

    mode: Mapped[MlScoringMode] = mapped_column(
        Enum(MlScoringMode, name="ml_scoring_mode", native_enum=False, length=10),
        nullable=False,
        default=MlScoringMode.AUTO,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
