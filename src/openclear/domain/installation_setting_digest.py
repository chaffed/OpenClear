# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from openclear.db.base import Base, new_uuid


class InstallationSettingDigest(Base):
    """The security-relevant installation settings a platform operator last acknowledged
    (DUAL_CONTROL_PLAN.md DC-D14; services/installation_settings.py): their digest and each
    one's own (never a secret itself), so the "Installation settings unchanged" check can say
    what changed outside OpenClear. One row. Readable by every bank's check, no bank's data."""

    __tablename__ = "installation_setting_digest"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    digest: Mapped[str] = mapped_column(String(64), nullable=False)
    settings: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)  # each setting's own digest
    acknowledged_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    acknowledged_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("user.id"), nullable=True)  # None: the first seen
