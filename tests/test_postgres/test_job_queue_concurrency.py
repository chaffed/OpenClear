# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Several workers draining the job queue at once (workers/jobs.py) never run a job
twice or skip one. Postgres only: the in-memory SQLite used elsewhere has one shared
connection, so it can't run workers in parallel. Runs only with OPENCLEAR_TEST_DATABASE_URL set."""

import threading
from collections import Counter

import pytest

from openclear.domain.job import Job, JobStatus
from openclear.workers import jobs
from tests.conftest import TEST_DATABASE_URL

pytestmark = [pytest.mark.skipif(not TEST_DATABASE_URL, reason="needs OPENCLEAR_TEST_DATABASE_URL (Postgres)"), pytest.mark.concurrency]

_WORKERS = 4
_JOBS = 40
_runs: Counter = Counter()
_lock = threading.Lock()


@jobs.handler("test.concurrent")
def _count(session, job):
    with _lock:
        _runs[job.payload["n"]] += 1


def test_parallel_workers_run_each_job_exactly_once(engine, db_session):
    _runs.clear()
    for n in range(_JOBS):
        jobs.enqueue(db_session, "test.concurrent", payload={"n": n})
    db_session.commit()

    start = threading.Barrier(_WORKERS)

    def worker(number: int) -> None:
        start.wait()
        while jobs.run_one(engine, f"worker-{number}"):
            pass

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(_WORKERS)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert _runs == Counter(range(_JOBS))  # every job, each exactly once
    db_session.expire_all()
    assert {j.status for j in db_session.query(Job).all()} == {JobStatus.SUCCEEDED}
