# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import uuid
from datetime import datetime

from sqlalchemy import JSON, BigInteger, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from openclear.db.base import Base, new_uuid


class PlatformActionEntry(Base):
    """The service provider's own action log (DUAL_CONTROL_PLAN.md DC4; services/platform_log_service.py):
    what the platform's staff and its API keys changed for the whole installation, chained and
    signed like a bank's action log. Written and read only from a session bound to no bank."""

    __tablename__ = "platform_action_log"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    sequence: Mapped[int] = mapped_column(BigInteger, nullable=False, unique=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("user.id"), nullable=True)
    actor_key_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("platform_api_key.id"), nullable=True)  # a platform API key
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    summary: Mapped[str] = mapped_column(String(500), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(50), nullable=False)
    resource_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    metadata_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    prev_entry_hash: Mapped[str | None] = mapped_column(String(200), nullable=True)
    entry_hash: Mapped[str] = mapped_column(String(200), nullable=False)  # the ECDSA signature over the entry and the one before
