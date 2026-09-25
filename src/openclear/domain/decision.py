# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import enum
import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, Enum, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from openclear.db.base import Base, new_uuid


class DecisionOutcome(str, enum.Enum):
    PAY = "pay"
    RETURN = "return"


class DecisionSource(str, enum.Enum):
    # A real human made this call — decision_service.py::decide(). The overwhelming
    # majority of rows, and the only value that ever existed before auto-disposition.
    HUMAN = "human"
    # services/auto_disposition_service.py, a fixed pay/return default applied because no
    # one decided before the exception's deadline.
    AUTO_DEFAULT = "auto_default"
    # services/auto_disposition_service.py, the ML model's own score picked the outcome.
    AUTO_ML = "auto_ml"


class Decision(Base):
    """The human pay/return decision on an exception — network-agnostic ground truth for
    ML training. `features_json` snapshots the feature vector as it was at decision time
    (via the network adapter's build_features()), because issued-item/authorization state
    can drift after the fact and retraining must learn from features-as-observed."""

    __tablename__ = "decision"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id"), nullable=False, index=True)
    exception_item_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("exception_item.id"), nullable=False, unique=True, index=True
    )

    outcome: Mapped[DecisionOutcome] = mapped_column(
        Enum(DecisionOutcome, name="decision_outcome", native_enum=False, length=10), nullable=False
    )
    reason_code: Mapped[str] = mapped_column(String(64), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Snapshotted from the selected AchReturnReason at decision time (see
    # services/decision_service.py::decide()) — only ever set for an ACH RETURN. Not a
    # NACHA return-reason code; it's the bank's own core-system posting code for the
    # reversal, captured here purely for staff visibility (see domain/ach_return_reason.py).
    return_transaction_code: Mapped[str | None] = mapped_column(String(10), nullable=True)

    submitted_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("user.id"), nullable=True)
    # Nullable since services/auto_disposition_service.py's system-generated decisions have
    # no human decider — see `source` below for how those are distinguished from a real one.
    decided_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("user.id"), nullable=True)
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    source: Mapped[DecisionSource] = mapped_column(
        Enum(DecisionSource, name="decision_source", native_enum=False, length=15),
        nullable=False,
        default=DecisionSource.HUMAN,
    )

    features_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
