# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import enum
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, Float, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column

from openclear.db.base import Base, new_uuid


class ExceptionItemSource(str, enum.Enum):
    LIVE = "live"
    # Created by services/fraud_training_service.py, not the real matching pipeline — see
    # that module for why: a known-fraud transaction can clear the real rules cleanly (no
    # OCR payee check, an authorized-looking ACH debit), so it needs a way into the
    # training set that doesn't depend on ever tripping an exception for real.
    TRAINING_BACKFILL = "training_backfill"


class ExceptionStatus(str, enum.Enum):
    OPEN = "open"
    PENDING_APPROVAL = "pending_approval"  # maker recommendation submitted, awaiting checker
    PAY = "pay"
    RETURN = "return"
    ESCALATED = "escalated"
    # Set only by services/bulk_upload_reversal_service.py when the underlying paid_item
    # is backed out and this exception hadn't been decided yet (still OPEN or
    # PENDING_APPROVAL) — an already-PAY/RETURN/ESCALATED exception is never overridden
    # this way, since a human decision already happened.
    WITHDRAWN = "withdrawn"


class ExceptionItem(Base):
    """Network-agnostic. One row per transaction that failed a network's matching rules.
    `source_item_id` is a generic pointer (no DB-level FK) resolved via
    networks.registry.get_adapter(network_code).load_source_item() — see networks/base.py
    for why: a typed FK column per network doesn't scale past a couple of networks."""

    __tablename__ = "exception_item"

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

    decision_deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    source: Mapped[ExceptionItemSource] = mapped_column(
        Enum(ExceptionItemSource, name="exception_item_source", native_enum=False, length=20),
        nullable=False,
        default=ExceptionItemSource.LIVE,
    )
    # True when this TRAINING_BACKFILL exception points at a source_item_id that already
    # had a prior LIVE exception decided PAY/RETURN — i.e. this label is correcting a past
    # live decision, not just adding a fresh one. Display-only (see
    # services/fraud_training_service.py for how it's computed).
    is_correction: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # One-way soft-retraction, mirrors BulkUploadFile.backed_out_at/backed_out_by_user_id —
    # only ever set on a TRAINING_BACKFILL exception (see fraud_training_service.retract_*).
    retracted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Only meaningful for TRAINING_BACKFILL (fraud-training) examples from a bank on the
    # shared model: such an example feeds the shared model only once the platform
    # operator has approved it, so one bank can't quietly poison scoring for all of them
    # (ml/train.py). Live exceptions and bank-only banks' examples ignore it.
    shared_training_approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    retracted_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("user.id"), nullable=True)
