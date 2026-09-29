# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""The platform operator's view of background jobs (api/v1/platform_jobs.py)."""

from openclear.domain.job import Job, JobStatus
from openclear.services import platform_api_key_service
from openclear.workers import jobs

_ran: list = []


@jobs.handler("test.platform_retry")
def _record(session, job):
    _ran.append(job.id)


def _operator_headers(db_session, scopes=("operations",)):
    _row, raw_key = platform_api_key_service.generate_and_create(db_session, "Ops", scopes)
    db_session.commit()
    return {"X-Api-Key": raw_key}


def _failed_job(db_session, kind="test.platform_retry"):
    job = jobs.enqueue(db_session, kind)
    job.status, job.attempts, job.last_error = JobStatus.FAILED, 5, "Traceback ... RuntimeError: disk full"
    db_session.commit()
    return job


def test_summary_and_failed_list(client, db_session):
    headers = _operator_headers(db_session)
    job = _failed_job(db_session)

    summary = client.get("/api/v1/platform/jobs/summary", headers=headers).json()
    assert summary["failed"] == 1

    failed = client.get("/api/v1/platform/jobs?status_filter=failed", headers=headers)
    assert failed.status_code == 200
    assert [j["id"] for j in failed.json()] == [str(job.id)]
    assert "disk full" in failed.json()[0]["last_error"]
    assert failed.headers["x-total-count"] == "1"


def test_retrying_a_failed_job_runs_it_again(client, db_session):
    _ran.clear()
    headers = _operator_headers(db_session)
    job = _failed_job(db_session)

    resp = client.post(f"/api/v1/platform/jobs/{job.id}/retry", headers=headers)

    assert resp.status_code == 200
    assert _ran == [job.id]  # inline mode in tests: it runs straight away
    db_session.expire_all()
    assert db_session.get(Job, job.id).status == JobStatus.SUCCEEDED


def test_only_a_failed_job_can_be_retried(client, db_session):
    headers = _operator_headers(db_session)
    job = jobs.enqueue(db_session, "test.platform_retry", run_after=None)
    db_session.commit()
    db_session.get(Job, job.id).status = JobStatus.RUNNING
    db_session.commit()

    assert client.post(f"/api/v1/platform/jobs/{job.id}/retry", headers=headers).status_code == 409


def test_a_key_without_the_operations_scope_is_refused(client, db_session):
    headers = _operator_headers(db_session, scopes=("usage",))

    assert client.get("/api/v1/platform/jobs/summary", headers=headers).status_code == 403
