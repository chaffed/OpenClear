# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import uuid
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from openclear.db.base import Base, new_uuid


class SecurityGroup(Base):
    """A named, tenant-defined set of permission keys (see auth/permissions.py for the
    catalog) — replaces the old fixed UserRole enum. Every tenant gets the 4 default
    groups (Admin/Preparer/Approver/Viewer, see auth/permissions.py::DEFAULT_SECURITY_GROUPS)
    seeded on creation, fully editable from there; groups are never deleted, only renamed
    or have their permissions changed, consistent with this app's no-hard-delete
    philosophy elsewhere (void/cancel, never DROP)."""

    __tablename__ = "security_group"
    __table_args__ = (UniqueConstraint("tenant_id", "name", name="uq_security_group_tenant_name"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    permissions: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    # Whether a customer's own administrators may give this group to their people
    # (CUSTOMER_ADMIN_PLAN.md). They can't edit groups, only choose among these.
    offered_to_customers: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
