# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""workers/leader_lock.py: with several instances on one Postgres database, each scheduled
job runs on only one of them per tick. No Postgres server is available to the test suite,
so the Postgres path runs against a fake engine that records the SQL it's sent."""

import pytest

from openclear.config import get_settings
from openclear.workers import scheduler
from openclear.workers.leader_lock import lock_key, run_exclusively


class _FakeResult:
    def __init__(self, value):
        self._value = value

    def scalar(self):
        return self._value


class _FakeConnection:
    def __init__(self, engine):
        self.engine = engine

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, statement, params):
        sql = str(statement)
        self.engine.statements.append((sql, params["key"]))
        if "pg_try_advisory_lock" in sql:
            return _FakeResult(self.engine.lock_available)
        return _FakeResult(True)

    def commit(self):
        pass

    def invalidate(self):
        self.engine.invalidated = True


class _FakeDialect:
    name = "postgresql"


class _FakePostgresEngine:
    dialect = _FakeDialect()

    def __init__(self, *, lock_available: bool):
        self.lock_available = lock_available
        self.statements: list[tuple[str, int]] = []
        self.invalidated = False

    def connect(self):
        return _FakeConnection(self)


def test_lock_key_is_stable_and_distinct_per_job():
    assert lock_key("ml_retrain_job") == lock_key("ml_retrain_job")
    assert lock_key("ml_retrain_job") != lock_key("dropbox_import_job")
    assert -(2**63) <= lock_key("ml_retrain_job") < 2**63


def test_non_postgres_database_just_runs_the_job(db_session):
    ran = []
    assert run_exclusively("any_job", lambda: ran.append(True), engine=db_session.get_bind()) is True
    assert ran == [True]


def test_postgres_runs_the_job_when_the_lock_is_free_and_releases_it():
    engine = _FakePostgresEngine(lock_available=True)
    ran = []

    assert run_exclusively("ml_retrain_job", lambda: ran.append(True), engine=engine) is True

    assert ran == [True]
    key = lock_key("ml_retrain_job")
    assert [(("unlock" in sql), k) for sql, k in engine.statements] == [(False, key), (True, key)]


def test_postgres_skips_the_job_when_another_instance_holds_the_lock():
    engine = _FakePostgresEngine(lock_available=False)
    ran = []

    assert run_exclusively("ml_retrain_job", lambda: ran.append(True), engine=engine) is False

    assert ran == []
    assert not any("unlock" in sql for sql, _ in engine.statements)


def test_postgres_releases_the_lock_even_when_the_job_fails():
    engine = _FakePostgresEngine(lock_available=True)

    def boom():
        raise RuntimeError("job failed")

    with pytest.raises(RuntimeError):
        run_exclusively("ml_retrain_job", boom, engine=engine)

    assert "pg_advisory_unlock" in engine.statements[-1][0]


def test_every_scheduled_job_goes_through_the_lock(monkeypatch):
    settings = get_settings()
    for flag in ("enable_ml_scheduler", "auto_import_enabled", "notifications_enabled", "enable_disposition_scheduler", "demo_tenant_enabled"):
        monkeypatch.setattr(settings, flag, True)
    monkeypatch.setattr(settings, "demo_tenant_reset_interval_minutes", 60)

    locked_job_ids = []
    monkeypatch.setattr(scheduler, "run_exclusively", lambda job_id, job: locked_job_ids.append(job_id))

    sched = scheduler.start_scheduler()
    try:
        sched.pause()
        jobs = sched.get_jobs()
        assert {job.id for job in jobs} == {
            "ml_retrain_job", "dropbox_import_job", "notification_dispatch_job",
            "sweep_expired_dispositions_job", "decision_lock_job", "continuity_snapshot_job", "demo_reset_job",
        }
        for job in jobs:
            job.func()
    finally:
        scheduler.stop_scheduler()

    assert sorted(locked_job_ids) == sorted(job.id for job in jobs)
