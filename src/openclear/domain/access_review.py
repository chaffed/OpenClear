# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from openclear.db.base import Base, new_uuid


class AccessReview(Base):
    """A review of who has access (CONTROLS_PLAN.md CT3; services/access_review_service.py): the
    bank's of every membership, or a self-managing customer's of its own people. It lists each
    membership as it stood when the review started; a reviewer confirms, marks changed or
    removes each; it completes when every one is decided, and that's recorded in the signed
    action log with a digest of the decisions. Evidence for B-AC-6(7)-01 and C-AC-6(7)-01."""

    __tablename__ = "access_review"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id"), nullable=False, index=True)
    customer_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("customer.id"), nullable=True, index=True)  # None: the bank's
    started_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("user.id"), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("user.id"), nullable=True)
    item_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    decisions_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)


class AccessReviewItem(Base):
    """One membership in an access review, as it stood when the review started, and what the
    reviewer decided: `confirmed`, `changed` (with a note on what) or `removed` (deactivated)."""

    __tablename__ = "access_review_item"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id"), nullable=False, index=True)
    review_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("access_review.id"), nullable=False, index=True)
    membership_id: Mapped[uuid.UUID] = mapped_column(nullable=False)  # not a foreign key: kept if the membership is later removed
    who: Mapped[str] = mapped_column(String(320), nullable=False)  # the person's email, or "API client: <name>"
    role: Mapped[str] = mapped_column(String(200), nullable=False)
    scope: Mapped[str] = mapped_column(String(300), nullable=False)  # "Bank-wide", or the customer's name
    accounts: Mapped[str] = mapped_column(String(500), nullable=False)
    last_sign_in: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decision: Mapped[str | None] = mapped_column(String(10), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    decided_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("user.id"), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
