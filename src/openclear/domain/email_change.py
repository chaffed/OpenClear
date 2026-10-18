# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column

from openclear.db.base import Base, new_uuid


class EmailChange(Base):
    """A change of email waiting for the new address to confirm it (OWASP_PLAN.md OW-D31; ADR
    0006): asked for by the person or an administrator, applied when the link sent to the new
    address is followed. Only the link's SHA-256 is kept. Like the person, it belongs to no bank
    (tenant_id only records which bank's page asked)."""

    __tablename__ = "email_change"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("user.id"), nullable=False, index=True)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tenant.id"), nullable=True)
    new_email: Mapped[str] = mapped_column(String(320), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    requested_by_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("user.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
