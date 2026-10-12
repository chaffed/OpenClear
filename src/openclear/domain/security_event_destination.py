# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from openclear.db.base import Base, new_uuid


class SecurityEventDestination(Base):
    """Where security events are delivered (SIEM_PLAN.md S3; services/siem/): a syslog
    collector, a Splunk HTTP Event Collector or an HTTPS endpoint.

    `tenant_id` is a bank's own destination, which receives only that bank's events (one
    per bank, set from the control panel). Null is the installation's, which receives every
    bank's events: it's written from configuration (config.Settings.siem_*) by the delivery
    job, and only `cursor` and the delivery record are its own.

    `cursor` is the id of the last security event delivered; delivery is in order and at
    least once, so a receiver de-duplicates on the event id. Like `security_event`, this
    table has no row-level security: delivery reads every bank's destinations. Every read
    from a request filters by bank in the service."""

    __tablename__ = "security_event_destination"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tenant.id"), nullable=True, unique=True)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)  # syslog, splunk_hec, https
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # syslog: host, port, transport (tls, tcp, udp) and message format (json, cef, leef).
    host: Mapped[str | None] = mapped_column(String(255), nullable=True)
    port: Mapped[int | None] = mapped_column(Integer, nullable=True)
    transport: Mapped[str | None] = mapped_column(String(10), nullable=True)
    format: Mapped[str] = mapped_column(String(10), nullable=False, default="json")
    # Splunk HEC and HTTPS: the endpoint, and a token sent as its authorization (encrypted,
    # auth/crypto.py). For HEC, the index and source type are optional.
    url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    token_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    splunk_index: Mapped[str | None] = mapped_column(String(80), nullable=True)
    splunk_sourcetype: Mapped[str | None] = mapped_column(String(80), nullable=True)

    cursor: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    delivered_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    failing_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    consecutive_failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, onupdate=func.now())
