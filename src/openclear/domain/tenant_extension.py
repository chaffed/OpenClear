# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from openclear.db.base import Base, new_uuid


class TenantExtension(Base):
    """A plugin's import format or decision layout a bank has turned on (PLUGIN_PLAN.md PL2;
    services/plugin_service.py). Built-in ones need no row; they're always on. Stored by
    the entry's label ("paid_items/fiserv_fixed"), so a row outlives the plugin being
    removed and comes back into force if it's reinstalled."""

    __tablename__ = "tenant_extension"
    __table_args__ = (UniqueConstraint("tenant_id", "point", "name", name="uq_tenant_extension"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id"), nullable=False, index=True)
    point: Mapped[str] = mapped_column(String(40), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    enabled_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("user.id"), nullable=True)
    enabled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
