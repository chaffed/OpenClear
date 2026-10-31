# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Maintenance mode (CONTINUITY_PLAN.md BC-D4): sign-in and reading work, writes answer 503 with
the banner's words (the data export excepted), the cut-off sweep pauses and the pause counts as a
gap when the mode ends, and the Controls page says so."""

from datetime import datetime, timedelta, timezone

from openclear.config import get_settings
from openclear.domain.scheduled_job_run import ScheduledJobRun
from openclear.services import continuity_service, controls_service
from tests.test_web.test_staff_and_operator import _login


def test_reads_and_sign_in_work_and_writes_are_refused(client, db_session, tenant_factory, monkeypatch):
    tenant, _account, users = tenant_factory.make(slug="maint")
    monkeypatch.setattr(get_settings(), "maintenance_mode", True)
    csrf = _login(client, tenant.slug, users["admin"].email)  # signing in is allowed
    page = client.get("/ui/exceptions")
    assert page.status_code == 200 and "OpenClear is being restored" in page.text  # reading works, with the banner
    refused = client.post("/ui/settings/incident-contacts", data={"csrf_token": csrf, "contact_0_name": "x", "contact_0_email": "x@y.example"})
    assert refused.status_code == 503 and "being restored" in refused.text and refused.headers["Retry-After"] == "600"
    api = client.post("/api/v1/decision-batches", headers={"Authorization": "Bearer nope"}, json={})
    assert api.status_code == 503 and "being restored" in api.json()["detail"]
    export = client.post("/ui/settings/data-export", data={"csrf_token": csrf}, follow_redirects=False)
    assert export.status_code != 503  # the one write that must still work
    assert controls_service._maintenance_mode().status == "WARN"
    monkeypatch.setattr(get_settings(), "maintenance_mode", False)
    assert controls_service._maintenance_mode().status == "PASS"


def test_the_sweep_pauses_and_the_pause_is_a_gap_afterwards(db_session, tenant_factory, monkeypatch, session_factory):
    from openclear.workers import tasks

    tenant, account, _users = tenant_factory.make(slug="maint-gap")
    monkeypatch.setattr(tasks, "get_session_factory", lambda: session_factory)
    monkeypatch.setattr(get_settings(), "maintenance_mode", True)
    tasks.sweep_expired_dispositions_job()
    db_session.expire_all()
    marker = db_session.get(ScheduledJobRun, continuity_service.PAUSED_MARKER)
    assert marker is not None
    started = marker.last_started_at.replace(tzinfo=timezone.utc) if marker.last_started_at.tzinfo is None else marker.last_started_at
    tasks.sweep_expired_dispositions_job()  # still paused: the marker keeps its first time
    db_session.expire_all()
    assert db_session.get(ScheduledJobRun, continuity_service.PAUSED_MARKER).last_started_at.replace(tzinfo=timezone.utc) == started.replace(tzinfo=timezone.utc)

    monkeypatch.setattr(get_settings(), "maintenance_mode", False)
    now = datetime.now(timezone.utc) + timedelta(minutes=1)
    assert continuity_service.gap_before(db_session, now) == (started, now)  # the pause, as a gap
    assert db_session.get(ScheduledJobRun, continuity_service.PAUSED_MARKER) is None  # once
    assert continuity_service.gap_before(db_session, now) is None
