# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column

from openclear.db.base import Base, new_uuid


class EmbedLaunch(Base):
    """A one-time link that starts an embedded session (SDK_PLAN.md P2;
    services/embed_service.py): the bank's online banking asks for one, for the person
    signed in to it, and opens it in a frame. Only the code's SHA-256 is kept; it's good for
    a minute and once."""

    __tablename__ = "embed_launch"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id"), nullable=False, index=True)
    code_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("user.id"), nullable=False)
    membership_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant_membership.id"), nullable=False)
    api_client_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("api_client.id"), nullable=True)
    path: Mapped[str] = mapped_column(String(500), nullable=False)
    parent_origin: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
