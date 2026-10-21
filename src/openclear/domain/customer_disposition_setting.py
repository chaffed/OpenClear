# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from openclear.db.base import Base, new_uuid


class DispositionMode(str, enum.Enum):
    # Default — no auto-decision; an exception whose deadline passes just stays open,
    # exactly like today's behavior before this feature existed.
    NONE = "none"
    FIXED_PAY = "fixed_pay"
    FIXED_RETURN = "fixed_return"
    # Re-scores fresh via ml.predict.score_exception() at decision time and picks
    # PAY/RETURN from the model's own probability — see services/auto_disposition_service.py.
    ML_DETERMINED = "ml_determined"


class CustomerDispositionSetting(Base):
    """One row per (customer, network) a bank admin has ever touched the default
    disposition for — absence of a row means NONE (no auto-decision), same "only grows on
    explicit override" convention as CustomerMlSetting (domain/customer_ml_setting.py),
    which this table mirrors column-for-column."""

    __tablename__ = "customer_disposition_setting"
    __table_args__ = (
        UniqueConstraint("customer_id", "network_code", name="uq_customer_disposition_setting_customer_network"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id"), nullable=False, index=True)
    customer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("customer.id"), nullable=False, index=True)
    network_code: Mapped[str] = mapped_column(ForeignKey("payment_network.code"), nullable=False)

    mode: Mapped[DispositionMode] = mapped_column(
        Enum(DispositionMode, name="disposition_mode", native_enum=False, length=20),
        nullable=False,
        default=DispositionMode.NONE,
    )
    # None = fall back to config.Settings.default_disposition_response_window_hours — same
    # nullable-override-falls-back-to-global-default shape as
    # Tenant.access_token_expire_minutes/data_export_timeout_seconds
    # (services/tenant_service.py::set_session_timeouts/set_data_export_timeout).
    response_window_hours: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # A cut-off agreed with this customer ("14:00", the bank's time zone), in place of the
    # bank's for this network (UI_PLAN.md UI-D13). Set by bank staff; None uses the bank's.
    cutoff_time: Mapped[str | None] = mapped_column(String(5), nullable=True)
    # Only meaningful when `mode` could produce an ACH RETURN (FIXED_RETURN, or
    # ML_DETERMINED landing on RETURN) — an ACH return needs a reason from the tenant's own
    # AchReturnReason catalog, same rule decision_service.decide() enforces for a human. If
    # this is unset when needed, that customer+ach auto-decisioning is skipped rather than
    # inventing a reason — see auto_disposition_service.auto_decide_exception.
    default_ach_return_reason_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("ach_return_reason.id"), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
