# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""The restore drill (CONTINUITY_PLAN.md BC-D2): a SQLite copy of the test database is restored
into a scratch file, migrated, its chains verified, its files reconciled, and the signed result
recorded; the Controls page then shows it as the last restore test."""

import importlib.util
import sqlite3
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select

from openclear.domain.restore_test import RestoreTest
from openclear.services import controls_service, evidence_service

ROOT = Path(__file__).resolve().parents[2]


def _drill():
    spec = importlib.util.spec_from_file_location("restore_drill", ROOT / "scripts" / "restore_drill.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_a_drill_restores_migrates_verifies_and_records(db_session, tenant_factory, tmp_path, engine):
    if engine.dialect.name != "sqlite":
        pytest.skip("the drill's SQLite path copies the file; the Postgres path is pg_restore, exercised by the host's own drill")
    tenant_factory.make(slug="drill-bank")
    db_session.commit()
    dump = tmp_path / "copy.sqlite"
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        conn.exec_driver_sql("VACUUM INTO ?", (str(dump),))
    scratch = tmp_path / "scratch" / "restored.sqlite"

    ok, steps = _drill().run_drill(dump, f"sqlite:///{scratch}", {"completed_at": "2026-10-31T02:00:00+00:00",
                                                                   "database": {"sha256": steps_sha(dump)}}, record_in=db_session)
    assert ok, steps
    assert steps["restore"]["method"].startswith("copied") and steps["migrate"]["to"] and steps["chains"]["banks"] >= 1
    assert steps["chains"]["broken"] == [] and steps["files"]["ok"] and steps["manifest"]["sha256_matches"]
    with sqlite3.connect(scratch) as conn:
        assert conn.execute("select count(*) from tenant where slug = 'drill-bank'").fetchone() == (1,)

    [record] = db_session.execute(select(RestoreTest)).scalars().all()
    assert record.ok and evidence_service.verify(record.certificate) and record.backup_completed_at is not None
    assert controls_service._recovery_objectives(db_session).evidence["last_restore_drill"]["certificate_sha256"] == record.certificate_sha256


def steps_sha(path: Path) -> str:
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_a_broken_dump_fails_the_drill(db_session, tmp_path):
    dump = tmp_path / "broken.sqlite"
    dump.write_bytes(b"not a database")
    ok, steps = _drill().run_drill(dump, f"sqlite:///{tmp_path / 'scratch.sqlite'}", None, record_in=db_session)
    assert not ok and "error" in steps["migrate"]
    [record] = db_session.execute(select(RestoreTest)).scalars().all()
    assert not record.ok
