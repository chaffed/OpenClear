# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, Float, ForeignKey, Index, String, func
from sqlalchemy.orm import Mapped, mapped_column

from openclear.db.base import Base, new_uuid


class ExceptionItemSource(str, enum.Enum):
    LIVE = "live"


class ExceptionStatus(str, enum.Enum):
    OPEN = "open"
    PENDING_APPROVAL = "pending_approval"  # maker recommendation submitted, awaiting checker
    PAY = "pay"
    RETURN = "return"
    # Set only by services/bulk_upload_reversal_service.py when the underlying paid_item
    # is backed out and this exception hadn't been decided yet (still OPEN or
    # PENDING_APPROVAL) — an already-PAY/RETURN exception is never overridden
    # this way, since a human decision already happened.
    WITHDRAWN = "withdrawn"


class ExceptionItem(Base):
    """Network-agnostic. One row per transaction that failed a network's matching rules.
    `source_item_id` is a generic pointer (no DB-level FK) resolved via
    networks.registry.get_adapter(network_code).load_source_item() — see networks/base.py
    for why: a typed FK column per network doesn't scale past a couple of networks."""

    __tablename__ = "exception_item"
    __table_args__ = (
        Index("ix_exception_item_tenant_id_updated_at", "tenant_id", "updated_at"),
        # The work queue, its counts and the dashboard filter a bank's items by status and
        # sort by age; the expired-disposition sweep looks across banks by status and deadline.
        Index("ix_exception_item_tenant_status_created", "tenant_id", "status", "created_at"),
        Index("ix_exception_item_status_deadline", "status", "decision_deadline"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id"), nullable=False, index=True)
    network_code: Mapped[str] = mapped_column(ForeignKey("payment_network.code"), nullable=False, index=True)

    # Denormalized from the source item's own customer_id at creation time — see
    # issued_item.py's customer_id for the full rationale (same pattern on every
    # customer-scoped table). Also what per-customer ML training/scoring (ml/train.py,
    # ml/predict.py) groups on.
    customer_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("customer.id"), nullable=True, index=True)

    source_item_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    related_reference_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)

    exception_types: Mapped[str] = mapped_column(String(500), nullable=False)  # comma-separated ExceptionType values
    status: Mapped[ExceptionStatus] = mapped_column(
        Enum(ExceptionStatus, name="exception_status", native_enum=False, length=20),
        nullable=False,
        default=ExceptionStatus.OPEN,
    )

    ml_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    ml_model_version: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Holds a maker's proposed outcome while awaiting a checker's approval (dual-control
    # tenants only). Cleared into a real Decision row once decide() finalizes it — these
    # columns are working state, not the ground-truth ML training signal Decision is.
    recommended_outcome: Mapped[str | None] = mapped_column(String(10), nullable=True)
    recommended_reason_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Parallel to recommended_reason_code — see Decision.return_transaction_code for
    # what this is (and isn't).
    recommended_return_transaction_code: Mapped[str | None] = mapped_column(String(10), nullable=True)
    recommended_notes: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    # The catalog entry the maker picked when recommending an ACH return, so the checker's
    # form can pre-select exactly that entry. (It used to be recovered by matching
    # recommended_reason_code text back to the catalog, which breaks if a reason is renamed
    # or two share the same text.)
    recommended_ach_return_reason_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("ach_return_reason.id"), nullable=True
    )
    recommended_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("user.id"), nullable=True)
    # A change to a decision recommended under dual control, waiting for someone else to approve
    # it (UI_PLAN.md UI-D13): the recommended_* fields above hold the change, and the decision
    # already made stands until it's approved. Cleared when approved, or when the cut-off locks it.
    change_recommended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    decision_deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # A check's midnight deadline (UCC 4-104, 4-301): the end of the banking day after the one it
    # was presented on, in the bank's zone. The decision deadline is never later than the bank's
    # cut-off on that day (services/cutoff_service.py; REGULATORY_PLAN.md RG1). None for ACH.
    return_deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # When the exception.deadline_approaching webhook went out (services/webhook_service.py
    # warn_deadlines_approaching), so each exception is announced once.
    deadline_warned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # When the row last changed: what list endpoints' updated_since filters on, so a bank's
    # data pipeline fetches only what changed (docs/guides/bi.md).
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    source: Mapped[ExceptionItemSource] = mapped_column(
        Enum(ExceptionItemSource, name="exception_item_source", native_enum=False, length=20),
        nullable=False,
        default=ExceptionItemSource.LIVE,
    )
