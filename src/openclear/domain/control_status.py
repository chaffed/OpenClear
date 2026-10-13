# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from openclear.db.base import Base, new_uuid


class ControlStatus(Base):
    """Each control's last known status for a bank (services/controls_service.py), so a
    change (PASS to FAIL, say) becomes a `control.status.changed` security event a bank's
    SIEM can alert on, without polling the controls itself."""

    __tablename__ = "control_status"
    __table_args__ = (UniqueConstraint("tenant_id", "key"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id"), nullable=False, index=True)
    key: Mapped[str] = mapped_column(String(60), nullable=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False)
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
