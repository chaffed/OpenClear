# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""The service provider's action log (DUAL_CONTROL_PLAN.md DC4): every change the platform's staff
or its API keys make for the whole installation, chained and signed with the audit-log signing
key exactly as a bank's action log is (services/audit_log_service.py), and each a `platform.action`
security event for the installation's SIEM destination. Only from a session bound to no bank."""

import hashlib
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from openclear.domain.platform_action_entry import PlatformActionEntry
from openclear.services.audit_log_service import _canonical_fields, _sign, _verify

_LOCK = int.from_bytes(hashlib.sha256(b"openclear.platform_action_log").digest()[:8], "big", signed=True)


def _latest(session: Session) -> PlatformActionEntry | None:
    return session.execute(select(PlatformActionEntry).order_by(PlatformActionEntry.sequence.desc()).limit(1)).scalars().first()


def record(session: Session, *, action: str, summary: str, actor_user_id: uuid.UUID | None = None, actor_key_id: uuid.UUID | None = None,
           resource_type: str = "installation", resource_id: uuid.UUID | None = None, metadata: dict[str, Any] | None = None) -> PlatformActionEntry:
    """Append an entry, chained onto the last. The caller commits."""
    from openclear.services import security_events

    if session.get_bind().dialect.name == "postgresql":
        session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": _LOCK})
    prev = _latest(session)
    metadata = {**(metadata or {}), **({"api_key": str(actor_key_id)} if actor_key_id else {})} or None
    occurred_at = datetime.now(timezone.utc)
    prev_hash = prev.entry_hash if prev else None
    fields = _canonical_fields(occurred_at=occurred_at, actor_user_id=actor_user_id, channel="platform", action=action, summary=summary[:500],
                               resource_type=resource_type, resource_id=resource_id, metadata=metadata, prev_entry_hash=prev_hash)
    entry = PlatformActionEntry(sequence=(prev.sequence + 1) if prev else 1, occurred_at=occurred_at, actor_user_id=actor_user_id,
                                actor_key_id=actor_key_id, action=action, summary=summary[:500], resource_type=resource_type,
                                resource_id=resource_id, metadata_json=metadata, prev_entry_hash=prev_hash, entry_hash=_sign(fields))
    session.add(entry)
    session.flush()
    security_events.emit(session, "platform.action", tenant_id=None, actor_type="user" if actor_user_id else "api_key",
                         actor_id=actor_user_id or actor_key_id, target_type=resource_type, target_id=resource_id,
                         details={"action": action, "summary": summary[:300]}, channel="platform")
    return entry


def entries(session: Session, *, limit: int = 200) -> list[PlatformActionEntry]:
    return list(session.execute(select(PlatformActionEntry).order_by(PlatformActionEntry.sequence.desc()).limit(limit)).scalars())


def verify(session: Session) -> tuple[bool, int, int | None]:
    """(intact, entries checked, the first broken sequence)."""
    prev_hash, checked = None, 0
    for entry in session.execute(select(PlatformActionEntry).order_by(PlatformActionEntry.sequence)).scalars():
        fields = _canonical_fields(occurred_at=entry.occurred_at, actor_user_id=entry.actor_user_id, channel="platform", action=entry.action,
                                   summary=entry.summary, resource_type=entry.resource_type, resource_id=entry.resource_id,
                                   metadata=entry.metadata_json, prev_entry_hash=entry.prev_entry_hash)
        if entry.prev_entry_hash != prev_hash or not _verify(fields, entry.entry_hash):
            return False, checked, entry.sequence
        prev_hash, checked = entry.entry_hash, checked + 1
    return True, checked, None
