# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import enum
import uuid
from datetime import datetime

from sqlalchemy import JSON, BigInteger, Boolean, DateTime, Enum, ForeignKey, String, Text, UniqueConstraint, func
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


class Decision(Base):
    """The human pay/return decision on an exception — network-agnostic ground truth for
    ML training. `features_json` snapshots the feature vector as it was at decision time
    (via the network adapter's build_features()), because issued-item/authorization state
    can drift after the fact and retraining must learn from features-as-observed."""

    __tablename__ = "decision"
    __table_args__ = (UniqueConstraint("tenant_id", "feed_sequence", name="uq_decision_tenant_feed_sequence"),)

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
    # Its place in the bank's decision feed (services/decision_feed_service.py, SDK_PLAN.md
    # P0): 1, 2, 3... per bank, in the order decisions were committed, so a core reading
    # "everything after N" never misses one.
    feed_sequence: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # When the decision became final and went out (feed, webhook, decision files). Until the
    # item's cut-off it can be changed (UI_PLAN.md UI-D13); None means it still can. A decision
    # made with no cut-off ahead of it locks at once.
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class DecisionFeedCursor(Base):
    """How far one consumer of a bank's decision feed (its core, say) has confirmed it
    processed (POST /api/v1/decisions/acknowledge), so the bank can see one falling behind."""

    __tablename__ = "decision_feed_cursor"
    __table_args__ = (UniqueConstraint("tenant_id", "consumer", name="uq_decision_feed_cursor_consumer"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id"), nullable=False, index=True)
    consumer: Mapped[str] = mapped_column(String(100), nullable=False)
    through_sequence: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    updated_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("user.id"), nullable=True)


class DecisionRevision(Base):
    """An earlier version of a decision, kept when it was changed or undone before the cut-off
    (UI_PLAN.md UI-D13). Written once, never changed: with the action log, the full history of
    what was decided, by whom, and what replaced it."""

    __tablename__ = "decision_revision"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id"), nullable=False, index=True)
    exception_item_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("exception_item.id"), nullable=False, index=True)
    outcome: Mapped[DecisionOutcome] = mapped_column(
        Enum(DecisionOutcome, name="decision_outcome", native_enum=False, length=10), nullable=False
    )
    reason_code: Mapped[str] = mapped_column(String(64), nullable=False)
    return_transaction_code: Mapped[str | None] = mapped_column(String(10), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    submitted_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("user.id"), nullable=True)
    decided_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("user.id"), nullable=True)
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # Who changed or undid it, and when; `undone` when nothing replaced it.
    revised_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("user.id"), nullable=True)
    revised_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    undone: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
