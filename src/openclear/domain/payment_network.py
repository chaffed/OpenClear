# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import enum

from sqlalchemy import Boolean, Enum, String
from sqlalchemy.orm import Mapped, mapped_column

from openclear.db.base import Base


class SettlementTiming(str, enum.Enum):
    """How much lead time exists between a transaction being submitted and it becoming
    final/irrevocable. Checks and ACH are ASYNC_REVIEWABLE (a human can review within a
    window before settlement). A future real-time network (RTP, wire) would be
    SYNCHRONOUS_AUTHORIZATION: the fraud check must complete and return an authorize/block
    decision inline, before the payment is sent, because there is no return window after."""

    ASYNC_REVIEWABLE = "async_reviewable"
    SYNCHRONOUS_AUTHORIZATION = "synchronous_authorization"


class PaymentNetwork(Base):
    """Lookup table of supported payment networks (check, ach, ...). Deliberately a data
    row per network rather than a native DB enum column on exception_item/ml_model, so
    adding a new network is an INSERT + a new networks/<code>/ adapter module, not an
    ALTER TYPE migration."""

    __tablename__ = "payment_network"

    code: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    settlement_timing: Mapped[SettlementTiming] = mapped_column(
        Enum(SettlementTiming, name="settlement_timing", native_enum=False, length=32),
        nullable=False,
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
