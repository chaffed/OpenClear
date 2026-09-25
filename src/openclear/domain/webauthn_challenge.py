# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, LargeBinary, func
from sqlalchemy.orm import Mapped, mapped_column

from openclear.db.base import Base, new_uuid


class WebauthnChallengePurpose(str, enum.Enum):
    REGISTRATION = "registration"
    AUTHENTICATION = "authentication"


class WebauthnChallenge(Base):
    """A server-generated challenge awaiting the matching WebAuthn ceremony response.
    Single-use and short-lived: consumed (deleted) the moment it's verified, and rejected
    if `expires_at` has passed — both are required to prevent replay of an intercepted
    challenge. Stored in the DB (not an in-memory dict) so this works correctly across
    multiple app processes/workers, consistent with everything else in this project being
    DB-backed rather than introducing a cache/session-store dependency."""

    __tablename__ = "webauthn_challenge"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id"), nullable=False, index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("user.id"), nullable=False, index=True)

    challenge: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    purpose: Mapped[WebauthnChallengePurpose] = mapped_column(
        Enum(WebauthnChallengePurpose, name="webauthn_challenge_purpose", native_enum=False, length=20),
        nullable=False,
    )

    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
