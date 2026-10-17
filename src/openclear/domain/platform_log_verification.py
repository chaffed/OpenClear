# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime
from sqlalchemy.orm import Mapped, mapped_column

from openclear.db.base import Base, new_uuid


class PlatformLogVerification(Base):
    """One verification of the service provider's action log (services/platform_log_service.py),
    made from a session bound to no bank (daily, and on Platform → Changes). A bank's session
    can't read the provider's log under row-level security, but every bank's "Provider's action
    log intact" check reads its result here: whether it held, how many entries, where it broke.
    No bank's data, nothing from the log itself, so no RLS."""

    __tablename__ = "platform_log_verification"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    verified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    intact: Mapped[bool] = mapped_column(Boolean, nullable=False)
    checked: Mapped[int] = mapped_column(BigInteger, nullable=False)
    broken_at_sequence: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
