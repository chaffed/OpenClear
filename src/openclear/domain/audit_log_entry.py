# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import enum
import uuid
from datetime import datetime

from sqlalchemy import JSON, BigInteger, DateTime, Enum, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from openclear.db.base import Base, new_uuid


class AuditChannel(str, enum.Enum):
    WEB = "web"
    API = "api"


class AuditLogEntry(Base):
    """One entry per state-changing action, taken through either the web UI or the JSON
    API (`channel`) — see services/audit_log_service.py. Entries form a hash chain: each
    row's `entry_hash` is an ECDSA-P256/SHA256 signature (server-held
    `audit_log_signing_private_key_path`, a key pair distinct from every other signing
    key in this app) over its own fields plus the previous entry's hash. That's what
    makes the log tamper-*evident* — editing, deleting, or reordering any entry breaks
    the chain from that point forward, detectable by re-verifying it (verify_chain), not
    by trusting a stored flag. `occurred_at` is set in Python at call time rather than a
    DB server_default, because the signature must be computable — and the row's own
    id/timestamp already fixed — before insert."""

    __tablename__ = "audit_log_entry"
    # One chain per bank, numbered 1, 2, 3... The unique constraint is what stops two
    # concurrent writers from both chaining onto the same entry (a fork that verify_chain
    # would report as tampering): the second insert fails and retries instead.
    __table_args__ = (UniqueConstraint("tenant_id", "sequence", name="uq_audit_log_entry_tenant_sequence"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id"), nullable=False, index=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # Position in the bank's chain. Deliberately not among the signed fields (so entries
    # signed before it existed still verify): prev_entry_hash already proves the order,
    # and a renumbered entry breaks the chain walk in verify_chain anyway.
    sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)

    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("user.id"), nullable=True)
    channel: Mapped[AuditChannel] = mapped_column(Enum(AuditChannel, name="audit_channel", native_enum=False, length=10), nullable=False)
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    summary: Mapped[str] = mapped_column(String(500), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(50), nullable=False)
    resource_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    metadata_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # Widened from String(64) (a SHA-256 hex digest) to fit a hex-encoded ECDSA P-256
    # DER signature, which can run up to ~144 hex chars.
    prev_entry_hash: Mapped[str | None] = mapped_column(String(200), nullable=True)
    entry_hash: Mapped[str] = mapped_column(String(200), nullable=False)


class AuditLogCheckpoint(Base):
    """A run of a bank's oldest audit entries, moved out of the table into a signed archive
    file (services/audit_archive_service.py, SCALE_PLAN.md S7) so the table stops growing
    forever without losing anything. The chain still verifies: the first entry left in the
    table chains onto `through_entry_hash`, the archive's last entry, and the archive file
    is fingerprinted and signed so it can be checked on its own later."""

    __tablename__ = "audit_log_checkpoint"
    __table_args__ = (UniqueConstraint("tenant_id", "through_sequence", name="uq_audit_log_checkpoint_tenant_through"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id"), nullable=False, index=True)
    first_sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    through_sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    through_entry_hash: Mapped[str] = mapped_column(String(200), nullable=False)
    entry_count: Mapped[int] = mapped_column(BigInteger, nullable=False)
    archive_ref: Mapped[str] = mapped_column(String(1000), nullable=False)
    archive_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    archive_signature: Mapped[str] = mapped_column(String(200), nullable=False)
    oldest_occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    newest_occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
