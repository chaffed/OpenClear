# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column

from openclear.db.base import Base


class RevokedSession(Base):
    """One login session (the `sid` claim shared by its access and refresh tokens — see
    auth/security.py::create_session_tokens) that was ended by logging out. Its tokens
    are signed and would otherwise stay valid until they expired, so
    auth/deps.py::decode_and_build_context and services/session_service.py's refresh
    reject any token whose sid is listed here.

    Revoking ALL of a user's sessions ("sign out everywhere") doesn't use this table — it
    bumps User.token_version instead. Rows are only needed until the session would have
    expired anyway (`expires_at`), after which session_service prunes them.

    Not tenant-scoped (no tenant_id, no RLS): like User, a session belongs to an identity,
    and the only lookup is by primary key from an already-verified token."""

    __tablename__ = "revoked_session"

    session_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("user.id"), nullable=False, index=True)
    revoked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
