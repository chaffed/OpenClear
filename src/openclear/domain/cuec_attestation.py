# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import uuid
from datetime import date, datetime

from sqlalchemy import JSON, Boolean, Date, DateTime, ForeignKey, Index, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from openclear.db.base import Base, new_uuid


class CuecMapping(Base):
    """One of a bank's (or a customer's) own controls covering a CUEC (CONTROLS_PLAN.md CT4;
    services/attestation_service.py): its id in their inventory, name, owner and a link to it in
    their GRC tool. With `auto_accept`, OpenClear's proposed attestations for the CUEC are
    accepted and signed at once (an audited choice). Records the inventory version it was made
    against, so a changed CUEC flags it for review."""

    __tablename__ = "cuec_mapping"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id"), nullable=False, index=True)
    customer_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("customer.id"), nullable=True, index=True)  # None: the bank's
    cuec_id: Mapped[str] = mapped_column(String(30), nullable=False)
    control_ref: Mapped[str] = mapped_column(String(100), nullable=False)
    control_name: Mapped[str | None] = mapped_column(String(300), nullable=True)
    owner: Mapped[str | None] = mapped_column(String(200), nullable=True)
    link: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    auto_accept: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    inventory_version: Mapped[str] = mapped_column(String(10), nullable=False)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("user.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class CuecAttestation(Base):
    """A statement that a CUEC operated over a period (CONTROLS_PLAN.md CT4): proposed by OpenClear
    from its test results (`method="automated"`) or made by a person (`manual`), then accepted (and
    signed) or rejected. Accepted ones can't change: a correction is a new attestation that
    supersedes it. A row with no bank is the installation's (a host CUEC attested once by the
    platform's staff), readable by every bank and writable by none."""

    __tablename__ = "cuec_attestation"
    __table_args__ = (Index("ix_cuec_attestation_tenant_cuec_period", "tenant_id", "cuec_id", "period_end"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tenant.id"), nullable=True, index=True)
    customer_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("customer.id"), nullable=True, index=True)
    cuec_id: Mapped[str] = mapped_column(String(30), nullable=False)
    period_kind: Mapped[str] = mapped_column(String(10), nullable=False)  # monthly, quarterly, yearly; custom for the installation's
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)  # inclusive
    result: Mapped[str] = mapped_column(String(30), nullable=False)  # operated, operated_with_exceptions, not_operated, not_applicable, not_tested
    method: Mapped[str] = mapped_column(String(10), nullable=False)  # automated, manual
    statement: Mapped[str | None] = mapped_column(Text, nullable=True)
    evidence: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    files: Mapped[list] = mapped_column(JSON, nullable=False, default=list)  # [{name, sha256, storage_ref, size}]
    status: Mapped[str] = mapped_column(String(12), nullable=False)  # prepared, accepted, rejected, superseded
    prepared_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("user.id"), nullable=True)  # None: OpenClear
    prepared_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    accepted_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("user.id"), nullable=True)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    auto_accepted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    rejected_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    rejected_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("user.id"), nullable=True)
    inventory_version: Mapped[str] = mapped_column(String(10), nullable=False)
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("cuec_attestation.id"), nullable=True)
    signed_body: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # what the signature covers, as canonical JSON
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    signature: Mapped[str | None] = mapped_column(Text, nullable=True)  # ECDSA P-256 over sha256, hex
