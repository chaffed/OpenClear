# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import hashlib
import json
import random
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from pospay.auth.keys import load_private_key, load_public_key
from pospay.config import get_settings
from pospay.domain.audit_log_entry import AuditChannel, AuditLogEntry
from pospay.repositories.audit_log_repo import AuditLogRepository

_FIELD_SEPARATOR = "\x1f"  # ASCII unit separator — won't appear in any normal field value,
# so no combination of field values can be crafted to collide with a different field split.


def _normalize_datetime(dt: datetime) -> str:
    """SQLite drops tzinfo on reload (a freshly-created, still-in-session datetime comes
    back tz-aware; the same row read back in a later request comes back naive) — without
    normalizing, verify_chain would recompute a different hash for an untouched entry
    just because of *when* it re-read it, a false positive. Every datetime here is always
    UTC by construction, so a naive one is treated as already-UTC rather than guessed."""
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt.isoformat()


def _canonical_fields(
    *,
    occurred_at: datetime,
    actor_user_id: uuid.UUID | None,
    channel: str,
    action: str,
    summary: str,
    resource_type: str,
    resource_id: uuid.UUID | None,
    metadata: dict[str, Any] | None,
    prev_entry_hash: str | None,
) -> bytes:
    """A stable, unambiguous serialization of everything that makes an entry what it is —
    used both when an entry is first signed and whenever it's re-verified later, so the
    two must always agree bit-for-bit on a legitimate, unmodified entry."""
    parts = [
        _normalize_datetime(occurred_at),
        str(actor_user_id) if actor_user_id else "",
        channel,
        action,
        summary,
        resource_type,
        str(resource_id) if resource_id else "",
        json.dumps(metadata, sort_keys=True) if metadata else "",
        prev_entry_hash or "",
    ]
    return _FIELD_SEPARATOR.join(parts).encode("utf-8")


def _sign(fields_bytes: bytes) -> str:
    private_key = load_private_key(get_settings().audit_log_signing_private_key_path)
    return private_key.sign(fields_bytes, ec.ECDSA(hashes.SHA256())).hex()


def _verify(fields_bytes: bytes, signature: str) -> bool:
    """ECDSA signatures are non-deterministic (a fresh random nonce every _sign call),
    so — unlike the old HMAC scheme — this can't recompute-and-compare for equality; it
    must verify the stored signature against the public key instead."""
    public_key = load_public_key(get_settings().audit_log_signing_public_key_path)
    try:
        public_key.verify(bytes.fromhex(signature), fields_bytes, ec.ECDSA(hashes.SHA256()))
        return True
    except (InvalidSignature, ValueError):
        return False


@dataclass(frozen=True, slots=True)
class ChainVerificationResult:
    valid: bool
    checked_count: int
    broken_at_entry_id: uuid.UUID | None


def record_action(
    session: Session,
    tenant_id: uuid.UUID,
    *,
    actor_user_id: uuid.UUID | None,
    channel: Literal["web", "api"],
    action: str,
    summary: str,
    resource_type: str,
    resource_id: uuid.UUID | None = None,
    metadata: dict[str, Any] | None = None,
) -> AuditLogEntry:
    """Called from route handlers (both web and API — see the sweep across
    web/routers/*.py and api/v1/*.py) right after the mutating service call succeeds and
    before the request's db.commit(), so the audit entry and the business change commit
    together atomically. `action` is a short dotted code chosen at the call site (e.g.
    "issued_item.void"), not derived from a table name, so the log reads as a narrative
    rather than a raw row diff.

    Safe under concurrency: two requests at one bank can't both chain onto the same entry.
    On Postgres, writers for a bank queue on a transaction-scoped advisory lock. On every
    database the (tenant_id, sequence) unique constraint rejects a writer that lost a
    race; it then re-reads the latest entry and tries again, inside a savepoint, so the
    request's own changes are kept."""
    if session.get_bind().dialect.name == "postgresql":
        session.execute(select(func.pg_advisory_xact_lock(_chain_lock_key(tenant_id))))

    for attempt in range(_MAX_ATTEMPTS):
        entry = _build_entry(
            session, tenant_id, actor_user_id=actor_user_id, channel=channel, action=action, summary=summary,
            resource_type=resource_type, resource_id=resource_id, metadata=metadata,
        )
        try:
            with session.begin_nested():
                session.add(entry)
                session.flush()
            return entry
        except IntegrityError:
            if attempt == _MAX_ATTEMPTS - 1:
                raise
            # The winner may not have committed yet, so re-reading at once can lose again.
            # Only reachable without the Postgres lock (e.g. SQL Server).
            time.sleep(random.uniform(0.005, 0.05) * (attempt + 1))
    raise AssertionError("unreachable")


_MAX_ATTEMPTS = 10


def _chain_lock_key(tenant_id: uuid.UUID) -> int:
    digest = hashlib.sha256(f"pospay.audit_chain:{tenant_id}".encode()).digest()
    return int.from_bytes(digest[:8], "big", signed=True)


def _build_entry(
    session: Session,
    tenant_id: uuid.UUID,
    *,
    actor_user_id: uuid.UUID | None,
    channel: str,
    action: str,
    summary: str,
    resource_type: str,
    resource_id: uuid.UUID | None,
    metadata: dict[str, Any] | None,
) -> AuditLogEntry:
    prev = AuditLogRepository(session, tenant_id).latest()
    prev_hash = prev.entry_hash if prev else None
    occurred_at = datetime.now(timezone.utc)

    fields_bytes = _canonical_fields(
        occurred_at=occurred_at,
        actor_user_id=actor_user_id,
        channel=channel,
        action=action,
        summary=summary,
        resource_type=resource_type,
        resource_id=resource_id,
        metadata=metadata,
        prev_entry_hash=prev_hash,
    )

    return AuditLogEntry(
        tenant_id=tenant_id,
        sequence=(prev.sequence + 1) if prev else 1,
        occurred_at=occurred_at,
        actor_user_id=actor_user_id,
        channel=AuditChannel(channel),
        action=action,
        summary=summary,
        resource_type=resource_type,
        resource_id=resource_id,
        metadata_json=metadata,
        prev_entry_hash=prev_hash,
        entry_hash=_sign(fields_bytes),
    )


def list_entries(
    session: Session,
    tenant_id: uuid.UUID,
    *,
    limit: int | None = None,
    offset: int | None = None,
    order_by: Any = None,
) -> list[AuditLogEntry]:
    return AuditLogRepository(session, tenant_id).list(limit=limit, offset=offset, order_by=order_by)


def verify_chain(session: Session, tenant_id: uuid.UUID) -> ChainVerificationResult:
    """Walks every entry for this tenant in the order the chain was built and recomputes
    each hash from scratch — proof nothing has been edited, deleted, or reordered since
    it was written, not just that the stored values are internally consistent. Reads in
    batches, so memory stays flat however long the log is."""
    prev_hash: str | None = None
    index = 0
    for index, entry in enumerate(AuditLogRepository(session, tenant_id).iter_in_chain_order()):
        if entry.prev_entry_hash != prev_hash:
            return ChainVerificationResult(valid=False, checked_count=index, broken_at_entry_id=entry.id)

        fields_bytes = _canonical_fields(
            occurred_at=entry.occurred_at,
            actor_user_id=entry.actor_user_id,
            channel=entry.channel.value,
            action=entry.action,
            summary=entry.summary,
            resource_type=entry.resource_type,
            resource_id=entry.resource_id,
            metadata=entry.metadata_json,
            prev_entry_hash=entry.prev_entry_hash,
        )
        if not _verify(fields_bytes, entry.entry_hash):
            return ChainVerificationResult(valid=False, checked_count=index, broken_at_entry_id=entry.id)

        prev_hash = entry.entry_hash

    return ChainVerificationResult(valid=True, checked_count=index + 1 if prev_hash else 0, broken_at_entry_id=None)
