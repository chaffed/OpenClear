# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""The background-job queue (workers/jobs.py): claiming, retries with back-off, giving
up, recovering jobs whose worker died, deduplication, and the in-process worker thread."""

import threading
import time
from datetime import datetime, timedelta, timezone

import pytest

from openclear.config import get_settings
from openclear.db.tenancy import _RLS_TENANT_KEY
from openclear.domain.job import Job, JobStatus
from openclear.workers import jobs

_ran: list = []
_failures: dict[str, int] = {}


@jobs.handler("test.record")
def _record(session, job):
    _ran.append((job.payload.get("n"), session.info.get(_RLS_TENANT_KEY)))


@jobs.handler("test.fail")
def _fail(session, job):
    _failures[str(job.id)] = _failures.get(str(job.id), 0) + 1
    raise RuntimeError("boom")


@pytest.fixture(autouse=True)
def _reset():
    _ran.clear()
    _failures.clear()


def _job(db_session, job_id):
    db_session.expire_all()
    return db_session.get(Job, job_id)


def test_a_queued_job_runs_once_and_succeeds(db_session):
    job = jobs.enqueue(db_session, "test.record", payload={"n": 1})
    db_session.commit()

    assert jobs.drain(db_session.get_bind()) == 1
    assert jobs.drain(db_session.get_bind()) == 0

    assert _ran == [(1, None)]
    row = _job(db_session, job.id)
    assert (row.status, row.attempts, row.finished_at is not None) == (JobStatus.SUCCEEDED, 1, True)


def test_the_handler_runs_bound_to_the_jobs_bank(db_session, tenant_factory):
    tenant, _account, _users = tenant_factory.make(slug="jobs-bound")
    jobs.enqueue(db_session, "test.record", tenant_id=tenant.id, payload={"n": 2})
    db_session.commit()

    jobs.drain(db_session.get_bind())

    assert _ran == [(2, tenant.id)]


def test_a_duplicate_of_a_waiting_job_is_not_queued_again(db_session):
    first = jobs.enqueue(db_session, "test.record", payload={"n": 1}, dedupe_key="same")
    second = jobs.enqueue(db_session, "test.record", payload={"n": 1}, dedupe_key="same")
    db_session.commit()

    assert second.id == first.id
    jobs.drain(db_session.get_bind())
    assert len(_ran) == 1
    # Once it has run, the same key can be queued again.
    assert jobs.enqueue(db_session, "test.record", dedupe_key="same").id != first.id


def test_a_job_that_isnt_due_yet_waits(db_session):
    jobs.enqueue(db_session, "test.record", run_after=datetime.now(timezone.utc) + timedelta(hours=1))
    db_session.commit()

    assert jobs.drain(db_session.get_bind()) == 0
    assert _ran == []


def test_a_failing_job_is_retried_with_back_off_then_given_up(db_session, monkeypatch):
    monkeypatch.setattr(get_settings(), "job_max_attempts", 3)
    job = jobs.enqueue(db_session, "test.fail")
    db_session.commit()
    engine = db_session.get_bind()

    jobs.drain(engine)
    row = _job(db_session, job.id)
    assert (row.status, row.attempts) == (JobStatus.QUEUED, 1)
    assert "boom" in row.last_error
    assert row.run_after.replace(tzinfo=timezone.utc) > datetime.now(timezone.utc)  # backed off

    for _ in range(2):  # make it due again, twice more
        row.run_after = datetime.now(timezone.utc) - timedelta(seconds=1)
        db_session.commit()
        jobs.drain(engine)
        row = _job(db_session, job.id)

    assert (row.status, row.attempts, _failures[str(job.id)]) == (JobStatus.FAILED, 3, 3)
    assert row.finished_at is not None


def test_a_job_whose_worker_died_is_queued_again(db_session):
    job = jobs.enqueue(db_session, "test.record", payload={"n": 3})
    db_session.commit()
    row = _job(db_session, job.id)
    row.status, row.attempts, row.locked_by = JobStatus.RUNNING, 1, "dead-worker"
    row.heartbeat_at = datetime.now(timezone.utc) - timedelta(hours=1)
    db_session.commit()

    assert jobs.drain(db_session.get_bind()) == 1

    assert _ran == [(3, None)]
    assert _job(db_session, job.id).status == JobStatus.SUCCEEDED


def test_a_dead_workers_job_with_no_attempts_left_fails(db_session, monkeypatch):
    monkeypatch.setattr(get_settings(), "job_max_attempts", 1)
    job = jobs.enqueue(db_session, "test.record")
    db_session.commit()
    row = _job(db_session, job.id)
    row.status, row.attempts = JobStatus.RUNNING, 1
    row.heartbeat_at = datetime.now(timezone.utc) - timedelta(hours=1)
    db_session.commit()

    jobs.drain(db_session.get_bind())

    row = _job(db_session, job.id)
    assert row.status == JobStatus.FAILED
    assert "no heartbeat" in row.last_error
    assert _ran == []


def test_a_running_job_is_not_claimed_twice(db_session):
    job = jobs.enqueue(db_session, "test.record")
    db_session.commit()

    assert jobs.claim(db_session, "worker-a").id == job.id
    assert jobs.claim(db_session, "worker-b") is None


def test_an_unknown_kind_is_recorded_as_a_failure(db_session, monkeypatch):
    monkeypatch.setattr(get_settings(), "job_max_attempts", 1)
    job = jobs.enqueue(db_session, "test.nobody-handles-this")
    db_session.commit()

    jobs.drain(db_session.get_bind())

    row = _job(db_session, job.id)
    assert row.status == JobStatus.FAILED
    assert "No handler registered" in row.last_error


def test_the_in_process_worker_thread_picks_up_queued_work(db_session, monkeypatch):
    monkeypatch.setattr(get_settings(), "worker_mode", "in_process")
    monkeypatch.setattr(get_settings(), "worker_poll_seconds", 30.0)  # so only the wake-up can start it quickly
    monkeypatch.setattr(get_settings(), "job_stale_after_seconds", 3600)  # no heartbeat write during the test
    engine = db_session.get_bind()
    jobs.start_in_process_worker(engine)
    try:
        job = jobs.enqueue(db_session, "test.record", payload={"n": 4})
        db_session.commit()
        jobs.jobs_queued(engine)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and _job(db_session, job.id).status != JobStatus.SUCCEEDED:
            time.sleep(0.05)
    finally:
        jobs.stop_in_process_worker()

    assert _job(db_session, job.id).status == JobStatus.SUCCEEDED
    assert _ran == [(4, None)]
    assert not any(t.name == "openclear-worker" for t in threading.enumerate())
