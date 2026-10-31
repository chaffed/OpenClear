# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Backups measured (CONTINUITY_PLAN.md BC-D2, BC-D8): the Controls page reads the manifest the
host's backup job leaves and warns when it's missing or old; a SQLite installation makes its own
nightly copy and manifest."""

import json
import sqlite3
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine

from openclear import storage
from openclear.config import get_settings
from openclear.services import backup_service


def test_the_check_reads_the_hosts_manifest(db_session):
    missing = backup_service.freshness()
    assert missing.status == "WARN" and "No backup manifest" in missing.detail

    backup_service.write_manifest(database_bytes=734_003_200, database_sha256="ab" * 32, storage_areas={"check_images": {"objects": 3}},
                                  kept_at="s3://acme-backups/openclear/2026-10-31/", retention_days=35, made_by="backup.sh")
    fresh = backup_service.freshness()
    assert fresh.status == "PASS" and "734 MB" in fresh.detail and "1 storage area;" in fresh.detail and "35 days" in fresh.detail

    backup_service.write_manifest(database_bytes=1, database_sha256=None, storage_areas={}, kept_at=None, retention_days=None,
                                  made_by="backup.sh", completed_at=datetime.now(timezone.utc) - timedelta(hours=30))
    stale = backup_service.freshness()
    assert stale.status == "WARN" and "30 hours ago" in stale.detail

    storage.put(backup_service.AREA, backup_service.MANIFEST_KEY, b"not json")
    assert backup_service.freshness().status == "WARN"  # unreadable is as good as missing


def test_a_sqlite_installation_copies_itself_nightly(tmp_path, monkeypatch):
    db = tmp_path / "live.sqlite"
    with sqlite3.connect(db) as conn:
        conn.execute("create table t (n integer)")
        conn.execute("insert into t values (1), (2)")
    monkeypatch.setattr(get_settings(), "database_url", f"sqlite:///{db}")
    monkeypatch.setattr(get_settings(), "backup_manifest_storage_dir", str(tmp_path / "backups"))
    engine = create_engine(f"sqlite:///{db}")

    copy = backup_service.sqlite_copy(engine)
    assert copy is not None and copy.exists() and copy.parent == tmp_path / "backups" / "sqlite"
    with sqlite3.connect(copy) as conn:
        assert conn.execute("select count(*) from t").fetchone() == (2,)  # a consistent copy, with the rows
    manifest = json.loads(storage.read_key(backup_service.AREA, backup_service.MANIFEST_KEY))
    assert manifest["database"]["bytes"] == copy.stat().st_size and manifest["made_by"].startswith("openclear housekeeping")
    assert backup_service.freshness().status == "PASS"

    old = copy.parent / "openclear-20200101T000000Z.sqlite"
    old.write_bytes(b"x")
    import os
    os.utime(old, (0, 0))
    backup_service.sqlite_copy(engine)
    assert not old.exists() and len(list(copy.parent.glob("openclear-*.sqlite"))) == 2  # pruned to the days kept

    monkeypatch.setattr(get_settings(), "database_url", "postgresql+psycopg://x:y@localhost/z")
    assert backup_service.sqlite_copy(engine) is None  # the host's job backs Postgres up
