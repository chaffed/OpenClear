# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from openclear.db.base import Base, new_uuid


class UserDevice(Base):
    """A browser a person has signed in from (OWASP_PLAN.md OW-D30), known by a long-lived random
    cookie of which only the SHA-256 is kept. A sign-in from a browser without a known one emails
    the person. Belongs to the person, not a bank, like the person's password."""

    __tablename__ = "user_device"
    __table_args__ = (UniqueConstraint("user_id", "device_hash"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("user.id"), nullable=False, index=True)
    device_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    user_agent: Mapped[str | None] = mapped_column(String(300), nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
