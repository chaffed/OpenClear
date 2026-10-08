# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Moving a bank's oldest audit entries into signed archive files (SCALE_PLAN.md S7).

The audit log is append-only and grows with every action; at bulk-import volumes that's
tens of millions of rows a year per bank, and verify_chain walks all of them. With
`audit_archive_after_days` set, housekeeping moves entries older than that out of the
table:

1. Under the bank's audit lock, the entries are read oldest first and each is verified
   (signature, and the link to the one before, starting from the previous checkpoint). A
   broken chain is never archived: the run stops and says where.
2. They're written to a gzipped JSON Lines file (every field needed to re-verify them),
   which is stored (storage.py), fingerprinted and signed with the file-signing key.
3. An AuditLogCheckpoint records the range, the last entry's hash and the file; the
   entries are deleted in the same transaction.

The newest entry is never archived, so new entries always have one to chain onto.
verify_chain then starts from the latest checkpoint's hash, and verify_archives checks
every archive file and that the checkpoints link up."""

import gzip
import hashlib
import json
import logging
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from openclear import storage
from openclear.bulk_import.signing import sign_digest, verify_digest_signature
from openclear.domain.audit_log_entry import AuditLogCheckpoint, AuditLogEntry
from openclear.repositories.audit_log_repo import AuditLogRepository

logger = logging.getLogger(__name__)


class AuditChainBroken(Exception):
    """The entries to be archived don't verify; nothing was archived."""


@dataclass(frozen=True, slots=True)
class ArchiveCheck:
    valid: bool
    checked_archives: int
    checked_entries: int
    problem: str | None = None


def _utc_naive(value: datetime) -> datetime:
    """SQLite hands datetimes back without a timezone; everything here is UTC."""
    return value.astimezone(timezone.utc).replace(tzinfo=None) if value.tzinfo else value


def latest_checkpoint(session: Session, tenant_id: uuid.UUID) -> AuditLogCheckpoint | None:
    return session.execute(
        select(AuditLogCheckpoint).where(AuditLogCheckpoint.tenant_id == tenant_id)
        .order_by(AuditLogCheckpoint.through_sequence.desc()).limit(1)
    ).scalar_one_or_none()


def list_checkpoints(session: Session, tenant_id: uuid.UUID) -> list[AuditLogCheckpoint]:
    return list(session.execute(
        select(AuditLogCheckpoint).where(AuditLogCheckpoint.tenant_id == tenant_id).order_by(AuditLogCheckpoint.through_sequence)
    ).scalars())


def _record(entry: AuditLogEntry) -> dict:
    from openclear.services.audit_log_service import _normalize_datetime

    return {
        "id": str(entry.id),
        "sequence": entry.sequence,
        "occurred_at": _normalize_datetime(entry.occurred_at),
        "actor_user_id": str(entry.actor_user_id) if entry.actor_user_id else None,
        "channel": entry.channel.value,
        "action": entry.action,
        "summary": entry.summary,
        "resource_type": entry.resource_type,
        "resource_id": str(entry.resource_id) if entry.resource_id else None,
        "metadata": entry.metadata_json,
        "prev_entry_hash": entry.prev_entry_hash,
        "entry_hash": entry.entry_hash,
    }


def _verify_record(record: dict, prev_hash: str | None) -> bool:
    from openclear.services.audit_log_service import _canonical_fields, _verify

    if record["prev_entry_hash"] != prev_hash:
        return False
    fields = _canonical_fields(
        occurred_at=datetime.fromisoformat(record["occurred_at"]),
        actor_user_id=uuid.UUID(record["actor_user_id"]) if record["actor_user_id"] else None,
        channel=record["channel"],
        action=record["action"],
        summary=record["summary"],
        resource_type=record["resource_type"],
        resource_id=uuid.UUID(record["resource_id"]) if record["resource_id"] else None,
        metadata=record["metadata"],
        prev_entry_hash=record["prev_entry_hash"],
    )
    return _verify(fields, record["entry_hash"])


def archive_old_entries(session: Session, tenant_id: uuid.UUID, older_than_days: int) -> AuditLogCheckpoint | None:
    """One archive run for one bank. Returns the new checkpoint, or None if nothing was
    old enough. Raises AuditChainBroken (having changed nothing) if the entries don't
    verify. Commits."""
    from openclear.services.audit_log_service import _chain_lock_key

    if session.get_bind().dialect.name == "postgresql":
        session.execute(select(func.pg_advisory_xact_lock(_chain_lock_key(tenant_id))))
    repo = AuditLogRepository(session, tenant_id)
    newest = repo.latest()
    if newest is None:
        return None
    cutoff = session.execute(select(func.now())).scalar_one() - timedelta(days=older_than_days)
    previous = latest_checkpoint(session, tenant_id)
    prev_hash = previous.through_entry_hash if previous else None

    digest = hashlib.sha256()
    count, first, last = 0, None, None
    with tempfile.NamedTemporaryFile(suffix=".jsonl.gz", delete=False) as raw:
        spool = Path(raw.name)
    try:
        with gzip.open(spool, "wt", encoding="utf-8") as out:
            for entry in repo.iter_in_chain_order():
                if entry.sequence >= newest.sequence or _utc_naive(entry.occurred_at) >= _utc_naive(cutoff):
                    break
                record = _record(entry)
                if not _verify_record(record, prev_hash):
                    raise AuditChainBroken(f"Audit entry {entry.id} (sequence {entry.sequence}) doesn't verify; nothing was archived.")
                out.write(json.dumps(record, sort_keys=True) + "\n")
                prev_hash = entry.entry_hash
                first = first or record
                last = (record, entry.occurred_at)
                count += 1
        if count == 0:
            return None
        digest.update(spool.read_bytes())
        ref = storage.put_file("audit_archives", f"{tenant_id}/audit-{first['sequence']:012d}-{last[0]['sequence']:012d}.jsonl.gz", spool)
    finally:
        spool.unlink(missing_ok=True)

    checkpoint = AuditLogCheckpoint(
        tenant_id=tenant_id,
        first_sequence=first["sequence"],
        through_sequence=last[0]["sequence"],
        through_entry_hash=last[0]["entry_hash"],
        entry_count=count,
        archive_ref=ref,
        archive_sha256=digest.hexdigest(),
        archive_signature=sign_digest(digest.digest()),
        oldest_occurred_at=datetime.fromisoformat(first["occurred_at"]),
        newest_occurred_at=last[1],
    )
    session.add(checkpoint)
    session.execute(delete(AuditLogEntry).where(
        AuditLogEntry.tenant_id == tenant_id,
        AuditLogEntry.sequence >= checkpoint.first_sequence,
        AuditLogEntry.sequence <= checkpoint.through_sequence,
    ))
    session.commit()
    logger.info("Archived %d audit entries for tenant %s (sequence %d to %d)", count, tenant_id, checkpoint.first_sequence, checkpoint.through_sequence)
    return checkpoint


def verify_archives(session: Session, tenant_id: uuid.UUID) -> ArchiveCheck:
    """Every archive file: unchanged (fingerprint and signature), each entry verifying and
    chained to the one before, and each archive starting where the last one ended."""
    prev_hash: str | None = None
    entries = 0
    checkpoints = list_checkpoints(session, tenant_id)
    for number, checkpoint in enumerate(checkpoints):
        data = storage.read(checkpoint.archive_ref)
        digest = hashlib.sha256(data).digest()
        if digest.hex() != checkpoint.archive_sha256 or not verify_digest_signature(digest, checkpoint.archive_signature):
            return ArchiveCheck(False, number, entries, f"Archive {checkpoint.archive_ref} has changed since it was written.")
        for line in gzip.decompress(data).decode("utf-8").splitlines():
            record = json.loads(line)
            if not _verify_record(record, prev_hash):
                return ArchiveCheck(False, number, entries, f"Archived entry {record['id']} (sequence {record['sequence']}) doesn't verify.")
            prev_hash = record["entry_hash"]
            entries += 1
        if prev_hash != checkpoint.through_entry_hash:
            return ArchiveCheck(False, number, entries, f"Archive {checkpoint.archive_ref} doesn't end where its checkpoint says.")
    return ArchiveCheck(True, len(checkpoints), entries)


def archive_all_banks(session: Session, older_than_days: int) -> dict[uuid.UUID, int]:
    """Housekeeping's entry point: every bank, each bound in turn so RLS applies."""
    from openclear.db.tenancy import acting_as_tenant
    from openclear.domain.tenant import Tenant

    archived: dict[uuid.UUID, int] = {}
    for tenant_id in session.execute(select(Tenant.id)).scalars().all():
        with acting_as_tenant(session, tenant_id):
            try:
                checkpoint = archive_old_entries(session, tenant_id, older_than_days)
            except AuditChainBroken:
                session.rollback()
                logger.exception("Not archiving tenant %s's audit log", tenant_id)
                continue
        if checkpoint is not None:
            archived[tenant_id] = checkpoint.entry_count
    return archived
