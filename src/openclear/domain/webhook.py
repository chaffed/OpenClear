# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import enum
import uuid
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Enum, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from openclear.db.base import Base, new_uuid


class WebhookEndpoint(Base):
    """Where a bank wants events sent (SDK_PLAN.md P0; services/webhook_service.py): a URL,
    the event types it wants, and the secret its deliveries are signed with (Standard
    Webhooks). The secret is stored encrypted (auth/crypto.py) because it has to be used,
    not just checked."""

    __tablename__ = "webhook_endpoint"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id"), nullable=False, index=True)
    url: Mapped[str] = mapped_column(String(1000), nullable=False)
    description: Mapped[str | None] = mapped_column(String(200), nullable=True)
    event_types: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    secret_encrypted: Mapped[str] = mapped_column(String(500), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("user.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class WebhookDeliveryStatus(str, enum.Enum):
    PENDING = "pending"
    SUCCEEDED = "succeeded"
    FAILED = "failed"  # every retry used up


class WebhookDelivery(Base):
    """One event to one endpoint, and how sending it went. Written in the same transaction
    as whatever caused the event, so an event is never sent for a change that rolled back
    (and never lost for one that committed)."""

    __tablename__ = "webhook_delivery"
    __table_args__ = (Index("ix_webhook_delivery_endpoint_created", "endpoint_id", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id"), nullable=False, index=True)
    endpoint_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("webhook_endpoint.id"), nullable=False)
    message_id: Mapped[str] = mapped_column(String(64), nullable=False)  # webhook-id: the same on every retry
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    status: Mapped[WebhookDeliveryStatus] = mapped_column(
        Enum(WebhookDeliveryStatus, name="webhook_delivery_status", native_enum=False, length=20),
        nullable=False, default=WebhookDeliveryStatus.PENDING,
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
