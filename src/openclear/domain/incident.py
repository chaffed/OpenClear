# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import uuid
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from openclear.db.base import Base, new_uuid


class IncidentContact(Base):
    """Who at a bank hears of an incident at OpenClear's host (REGULATORY_PLAN.md RG3; 12 CFR
    304.24(a)(1): the bank's designated point of contact). `kind` "contact" for the people the bank
    names; "ceo" and "cio" for the fallback the rule falls back on (304.24(a)(2))."""

    __tablename__ = "incident_contact"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id"), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(10), nullable=False, default="contact")
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str | None] = mapped_column(String(255), nullable=True)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    phone: Mapped[str | None] = mapped_column(String(40), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class IncidentNotice(Base):
    """A notice the installation's operator sends to banks: an incident at the host, scheduled
    maintenance announced beforehand (304.24(b)), or an update or closure of an earlier one
    (`follows_id`). Belongs to the installation; each bank's copy is an IncidentNoticeDelivery."""

    __tablename__ = "incident_notice"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    kind: Mapped[str] = mapped_column(String(15), nullable=False)  # incident, maintenance, update, closed
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    determined_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    services_affected: Mapped[str | None] = mapped_column(String(500), nullable=True)
    bank_data_affected: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    follows_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("incident_notice.id"), nullable=True)
    issued_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("user.id"), nullable=True)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class IncidentNoticeDelivery(Base):
    """One bank's copy of a notice: whom it was emailed to and when, and who at the bank
    acknowledged it in the app and when. That's the evidence that the bank was told."""

    __tablename__ = "incident_notice_delivery"
    __table_args__ = (UniqueConstraint("notice_id", "tenant_id", name="uq_incident_notice_delivery"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id"), nullable=False, index=True)
    notice_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("incident_notice.id"), nullable=False, index=True)
    recipients: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    acknowledged_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("user.id"), nullable=True)
