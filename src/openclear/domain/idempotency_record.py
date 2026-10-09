# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Index, ForeignKey, Integer, LargeBinary, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from openclear.db.base import Base, new_uuid


class IdempotencyRecord(Base):
    """One `Idempotency-Key` an API caller sent on a write (SDK_PLAN.md P0;
    api/idempotency.py): the request it was first used with, and the response that request
    got, so a retry with the same key gets the same response instead of doing the work
    twice. Keys are per caller (the user or API client's service identity) within a bank.
    `status_code` is None while the first request is still running. Kept for
    `idempotency_key_hours`, then deleted by housekeeping."""

    __tablename__ = "idempotency_record"
    __table_args__ = (
        UniqueConstraint("tenant_id", "user_id", "key", name="uq_idempotency_record_caller_key"),
        Index("ix_idempotency_record_created_at", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id"), nullable=False, index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("user.id"), nullable=False)
    key: Mapped[str] = mapped_column(String(255), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)  # method, path, query and body
    status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    content_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    response_body: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
