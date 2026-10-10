# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import uuid
from datetime import datetime

from sqlalchemy import JSON, BigInteger, DateTime, ForeignKey, Index, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from openclear.db.base import Base


class SecurityEvent(Base):
    """One security event (SIEM_PLAN.md; services/security_events.py): a sign-in, a failure,
    a lockout, a change to who may do what, and so on. It's written in the same transaction
    as what it describes, read through the pull API, and later delivered to a bank's SIEM.

    `id` orders the events and is the feed's cursor. `tenant_id` is null for events with no
    bank, such as a failed sign-in naming a bank that doesn't exist. Like `job` and
    `notification`, this table has no row-level security: those events belong to no bank,
    and delivery reads every bank's. Every read filters by bank in the service."""

    __tablename__ = "security_event"
    __table_args__ = (Index("ix_security_event_tenant_id_id", "tenant_id", "id"),
                      Index("ix_security_event_occurred_at", "occurred_at"))

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tenant.id"), nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    category: Mapped[str] = mapped_column(String(30), nullable=False)
    outcome: Mapped[str] = mapped_column(String(10), nullable=False)  # success, failure, unknown
    severity: Mapped[str] = mapped_column(String(10), nullable=False)  # low, medium, high, critical
    reason: Mapped[str | None] = mapped_column(String(60), nullable=True)
    actor_type: Mapped[str | None] = mapped_column(String(20), nullable=True)  # user, api_client, system
    actor_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    actor_label: Mapped[str | None] = mapped_column(String(320), nullable=True)
    target_type: Mapped[str | None] = mapped_column(String(40), nullable=True)
    target_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    target_label: Mapped[str | None] = mapped_column(String(320), nullable=True)
    source_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(300), nullable=True)
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    edge_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    channel: Mapped[str | None] = mapped_column(String(10), nullable=True)
    audit_sequence: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    details: Mapped[dict | None] = mapped_column(JSON, nullable=True)
