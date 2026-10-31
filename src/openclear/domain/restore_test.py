# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import uuid
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from openclear.db.base import Base, new_uuid


class RestoreTest(Base):
    """A restore drill's result (scripts/restore_drill.py; CONTINUITY_PLAN.md BC-D2): a backup
    restored into a scratch database, migrated, its action logs verified, its files reconciled,
    and the outcome signed, so "the last restore test" is evidence rather than a date typed in.
    One row per drill, for the whole installation: no bank's data."""

    __tablename__ = "restore_test"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)
    ok: Mapped[bool] = mapped_column(Boolean, nullable=False)
    backup_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)  # from the manifest
    source: Mapped[str] = mapped_column(String(1000), nullable=False)  # the dump or copy restored
    detail: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)  # steps and what each found
    certificate: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # evidence_service.sign of the detail
    certificate_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
