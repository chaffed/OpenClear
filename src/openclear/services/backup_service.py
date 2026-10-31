# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Backups measured, not attested (CONTINUITY_PLAN.md BC-D2, BC-D8).

The host's backup job writes `latest.json` into the `backup_manifests` storage area when it
finishes (RUNBOOK.md "Backups" gives the shape); the `backup_freshness` check reads it and warns
when it's missing or older than `backup_max_age_hours`. A SQLite installation has no backup job
of its own, so housekeeping makes one: `VACUUM INTO` a dated copy (consistent without stopping
writers), puts it in the same area, and writes the manifest itself."""

import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy.engine import Engine, make_url

from openclear import storage
from openclear.config import get_settings

logger = logging.getLogger(__name__)

AREA = "backup_manifests"
MANIFEST_KEY = "latest.json"


@dataclass(frozen=True)
class Manifest:
    completed_at: datetime
    database_bytes: int | None
    database_sha256: str | None
    storage_areas: dict
    kept_at: str | None
    retention_days: int | None
    made_by: str | None

    @property
    def age(self) -> timedelta:
        return datetime.now(timezone.utc) - self.completed_at


def latest() -> Manifest | None:
    """The newest manifest, or None when the host's job hasn't left one (or left one that doesn't read)."""
    raw = storage.read_key(AREA, MANIFEST_KEY)
    if raw is None:
        return None
    try:
        data = json.loads(raw)
        completed = datetime.fromisoformat(str(data["completed_at"]))
        if completed.tzinfo is None:
            completed = completed.replace(tzinfo=timezone.utc)
        database = data.get("database") or {}
        return Manifest(completed, database.get("bytes"), database.get("sha256"), data.get("storage_areas") or {},
                        data.get("kept_at"), data.get("retention_days"), data.get("made_by"))
    except (ValueError, KeyError, TypeError) as exc:
        logger.warning("The backup manifest at %s/%s doesn't read: %s", AREA, MANIFEST_KEY, exc)
        return None


def freshness():
    """The control check (compliance/checks.py `backup_freshness`, installation scope)."""
    from openclear.services.controls_service import Control, PASS, WARN

    title = "Backups fresh"
    manifest = latest()
    limit = timedelta(hours=get_settings().backup_max_age_hours)
    if manifest is None:
        return Control("backup_freshness", title, WARN,
                       f"No backup manifest at {AREA}/{MANIFEST_KEY}. The host's backup job should write one when it finishes "
                       "(RUNBOOK.md, Backups); until it does, backups are known only from the host's own records.",
                       {"manifest": None, "verified_by_openclear": False})
    evidence = {"completed_at": manifest.completed_at.isoformat(), "database_bytes": manifest.database_bytes,
                "storage_areas": sorted(manifest.storage_areas), "kept_at": manifest.kept_at, "retention_days": manifest.retention_days,
                "made_by": manifest.made_by, "max_age_hours": get_settings().backup_max_age_hours}
    when = manifest.completed_at.strftime("%Y-%m-%d %H:%M UTC")
    hours = int(manifest.age.total_seconds() // 3600)
    if manifest.age > limit:
        return Control("backup_freshness", title, WARN, f"The last backup finished {when}, {hours} hours ago: more than the "
                       f"{get_settings().backup_max_age_hours} hours allowed (OPENCLEAR_BACKUP_MAX_AGE_HOURS).", evidence)
    size = f"{manifest.database_bytes / 1_000_000:,.0f} MB" if manifest.database_bytes else "size not stated"
    kept = f"; kept at {manifest.kept_at}" if manifest.kept_at else ""
    days = f" for {manifest.retention_days} days" if manifest.retention_days else ""
    return Control("backup_freshness", title, PASS, f"Last backup {when}: the database ({size}) and {len(manifest.storage_areas)} storage "
                   f"area{'s' if len(manifest.storage_areas) != 1 else ''}{kept}{days}.", evidence)


def write_manifest(*, database_bytes: int | None, database_sha256: str | None, storage_areas: dict, kept_at: str | None,
                   retention_days: int | None, made_by: str, completed_at: datetime | None = None) -> str:
    """Writes latest.json; the host's own job writes the same shape (RUNBOOK.md, Backups)."""
    body = {"completed_at": (completed_at or datetime.now(timezone.utc)).isoformat(), "database": {"bytes": database_bytes, "sha256": database_sha256},
            "storage_areas": storage_areas, "kept_at": kept_at, "retention_days": retention_days, "made_by": made_by}
    return storage.put(AREA, MANIFEST_KEY, json.dumps(body, indent=2).encode())


def sqlite_copy(engine: Engine) -> Path | None:
    """A SQLite installation's nightly backup (BC-D8): `VACUUM INTO` a dated copy in the backup
    area (uploaded when storage is a bucket), the manifest written, copies older than
    `sqlite_backup_keep_days` removed. None for any other database: the host's job backs it up."""
    settings = get_settings()
    url = make_url(settings.database_url)
    if url.get_backend_name() != "sqlite" or not url.database or url.database == ":memory:":
        return None
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")  # to the microsecond: two copies a second apart are two files
    local_dir = Path(settings.backup_manifest_storage_dir) / "sqlite"
    local_dir.mkdir(parents=True, exist_ok=True)
    target = local_dir / f"openclear-{stamp}.sqlite"
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        conn.exec_driver_sql("VACUUM INTO ?", (str(target),))
    digest, size = hashlib.sha256(target.read_bytes()).hexdigest(), target.stat().st_size
    kept_at = str(target)
    if storage.is_object_ref(storage.put(AREA, f"sqlite/{target.name}.probe", b"")):
        kept_at = storage.put_file(AREA, f"sqlite/{target.name}", target)  # moved into the bucket
    write_manifest(database_bytes=size, database_sha256=digest, storage_areas={a: {} for a in storage.AREAS if a != AREA},
                   kept_at=kept_at, retention_days=settings.sqlite_backup_keep_days, made_by="openclear housekeeping (VACUUM INTO)")
    cutoff = datetime.now(timezone.utc) - timedelta(days=settings.sqlite_backup_keep_days)
    for old in local_dir.glob("openclear-*.sqlite"):
        if datetime.fromtimestamp(old.stat().st_mtime, tz=timezone.utc) < cutoff:
            old.unlink(missing_ok=True)
    logger.info("SQLite backup written: %s (%d bytes)", kept_at, size)
    return target
