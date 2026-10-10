# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Long requests as background jobs (WAF_PLAN.md W3; MASTER_PLAN.md M2;
services/long_runs.py): a large replay, a long evidence export, and verifying the action
log, each a control run with a state. The test suite runs jobs inline, so a queued run is
done by the time the request returns; the queue itself is checked with jobs held."""

import json
from datetime import date, timedelta

from openclear.config import get_settings
from openclear.domain.control_run import ControlRun
from openclear.services import evidence_service, long_runs
from tests.conftest import TenantFactory, login_headers
from tests.test_auth.test_mrm import _check_history


def test_a_large_replay_runs_as_a_job(client, db_session, tenant_factory, monkeypatch):
    tenant, account, users = tenant_factory.make(slug="long-replay")
    letter = _check_history(db_session, tenant, account, users)
    monkeypatch.setattr(get_settings(), "long_request_sync_bytes", 10)  # anything bigger is a job
    response = client.post("/api/v1/mrm/replay", headers=login_headers(client, tenant.slug, users["admin"].email),
                           files={"file": ("jan.x937", letter)})
    # Inline workers finish it before the response, so it answers as done; the run went through the queue.
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["state"] == "done" and body["report"]["items"] == 4 and "input_ref" not in body["report"]
    from sqlalchemy import select

    from openclear.domain.job import Job

    jobs = db_session.execute(select(Job).where(Job.kind == long_runs.JOB_KIND)).scalars().all()
    assert [job.payload["run_id"] for job in jobs] == [body["id"]]  # it went through the queue
    assert db_session.get(ControlRun, __import__("uuid").UUID(body["id"])).kind == "mrm_replay"


def test_a_queued_replay_answers_202(client, db_session, tenant_factory, monkeypatch):
    from openclear.workers import jobs

    tenant, account, users = tenant_factory.make(slug="long-queued")
    letter = _check_history(db_session, tenant, account, users)
    monkeypatch.setattr(get_settings(), "long_request_sync_bytes", 10)
    monkeypatch.setattr(jobs, "jobs_queued", lambda *a, **k: None)  # hold the queue
    import openclear.workers.jobs as jobs_module

    monkeypatch.setattr(jobs_module, "jobs_queued", lambda *a, **k: None)
    response = client.post("/api/v1/mrm/replay", headers=login_headers(client, tenant.slug, users["admin"].email),
                           files={"file": ("jan.x937", letter)})
    assert response.status_code == 202 and response.json()["state"] == "queued"
    run_id = response.json()["id"]
    long_runs.execute(db_session, __import__("uuid").UUID(run_id))
    db_session.commit()
    status = client.get(f"/api/v1/mrm/runs/{run_id}", headers=login_headers(client, tenant.slug, users["admin"].email)).json()
    assert status["state"] == "done" and status["report"]["items"] == 4


def test_a_long_evidence_export_runs_as_a_job(client, db_session, tenant_factory, monkeypatch):
    import openclear.workers.jobs as jobs_module

    tenant, _account, users = tenant_factory.make(slug="long-evidence")
    headers = login_headers(client, tenant.slug, users["admin"].email)
    monkeypatch.setattr(jobs_module, "jobs_queued", lambda *a, **k: None)
    today = date.today()
    response = client.get(f"/api/v1/audit/export?start={today - timedelta(days=60)}&end={today}", headers=headers)
    assert response.status_code == 202
    status = response.json()
    assert status["state"] == "queued" and status["status_url"].startswith("/api/v1/audit/export/runs/")
    assert client.get(status["status_url"] + "/file", headers=headers).status_code == 409  # not ready yet

    long_runs.execute(db_session, __import__("uuid").UUID(status["id"]))
    db_session.commit()
    done = client.get(status["status_url"], headers=headers).json()
    assert done["state"] == "done" and done["download_url"].endswith("/file")
    file = client.get(done["download_url"], headers=headers)
    assert file.status_code == 200 and evidence_service.verify(json.loads(file.content))
    from openclear.bulk_import.signing import verify_signature

    assert verify_signature(file.content, file.headers["x-openclear-file-signature"])


def test_verification_is_a_run_the_controls_page_reads(client, db_session, tenant_factory):
    from openclear.services import controls_service

    tenant, _account, users = tenant_factory.make(slug="long-verify")
    csrf = None
    client.get("/ui/login")
    csrf = client.cookies.get("csrf_token")
    client.post("/ui/login", data={"tenant_slug": tenant.slug, "email": users["admin"].email, "password": TenantFactory.PASSWORD,
                                   "csrf_token": csrf, "next": "/ui/"})
    started = client.get("/ui/audit-log/verify", follow_redirects=False)
    assert started.status_code == 303 and "/ui/audit-log/verify?run=" in started.headers["location"]
    shown = client.get(started.headers["location"]).text
    assert "chain verified" in shown  # inline workers: already done
    latest = long_runs.latest_verification(db_session, tenant.id)
    assert latest is not None and latest.passed
    control = next(c for c in controls_service.status(db_session, tenant.id) if c.key == "audit_chain")
    assert control.status == "PASS" and control.evidence["run_id"] == str(latest.id)


def test_the_daily_verification(db_session, tenant_factory, session_factory, monkeypatch):
    from openclear.workers import tasks

    tenant, _a, _u = tenant_factory.make(slug="long-daily")
    monkeypatch.setattr("openclear.db.session.get_session_factory", lambda: session_factory)
    tasks.verify_action_logs_job()
    db_session.expire_all()
    assert long_runs.latest_verification(db_session, tenant.id).passed
