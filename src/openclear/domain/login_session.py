# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from openclear.db.base import Base


class LoginSession(Base):
    """One session (OWASP_PLAN.md OW-D29): the id in its tokens' `sid`, and enough to recognise it
    in a list (browser, address, when). Recorded on its first request and refreshed at most every
    few minutes (auth/deps.py); ended when it's signed out or revoked. Kept 30 days after it ends.
    Revocation itself is still revoked_session, checked on every request."""

    __tablename__ = "login_session"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)  # the token's sid
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id"), nullable=False, index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("user.id"), nullable=False, index=True)
    signed_in_with: Mapped[str | None] = mapped_column(String(20), nullable=True)  # password, sso, embed
    # The person's token_version when it started: "Sign out everywhere" bumps it, which ends this
    # session without touching its row.
    token_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    user_agent: Mapped[str | None] = mapped_column(String(300), nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
