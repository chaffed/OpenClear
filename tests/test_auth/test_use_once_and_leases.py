# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Links used once even when two requests arrive together, and background jobs fenced so a
worker whose job was handed on can't commit (FULL_REVIEW_PLAN.md FR2: R8, R11)."""

import pytest
from sqlalchemy.orm import Session

from openclear.domain.job import Job, JobStatus
from openclear.services import password_reset_service
from openclear.workers import jobs


def test_a_reset_link_opened_twice_sets_one_password(db_session, session_factory, tenant_factory, monkeypatch):
    tenant, _account, users = tenant_factory.make(slug="once-reset")
    monkeypatch.setattr("openclear.services.notification_service.email_configured", lambda: True)
    sent = {}
    monkeypatch.setattr("openclear.services.notification_service.notify_password_reset_link",
                        lambda session, user, link, **kw: sent.setdefault("link", link))
    password_reset_service.start(db_session, tenant, users["viewer"], requested_by_user_id=users["admin"].id, base_url="http://x")
    db_session.commit()
    token = sent["link"].rsplit("/", 1)[1]

    one, two = session_factory(), session_factory()
    try:
        opened_one, opened_two = password_reset_service.open_reset(one, token), password_reset_service.open_reset(two, token)
        assert opened_one and opened_two  # both requests saw it unused
        password_reset_service.complete(one, opened_one, "Brand-new-passphrase-1")
        one.commit()
        with pytest.raises(password_reset_service.ResetError, match="already been used"):
            password_reset_service.complete(two, opened_two, "Another-passphrase-22")
    finally:
        one.close()
        two.close()


def test_a_worker_whose_job_was_handed_on_cant_commit(db_session, engine):
    job = jobs.enqueue(db_session, "test.lease")
    db_session.commit()
    with Session(bind=engine) as session:
        assert jobs.claim(session, "worker-a").id == job.id
    db_session.expire_all()
    stored = db_session.get(Job, job.id)
    stored.locked_by = "worker-b"  # its heartbeat looked stale, and worker B claimed it
    db_session.commit()

    with Session(bind=engine) as session:
        session.info["openclear_job_lease"] = (job.id, "worker-a")
        with pytest.raises(jobs.LeaseLost):
            jobs.check_lease(session)
        session.info["openclear_job_lease"] = (job.id, "worker-b")
        jobs.check_lease(session)  # the owner carries on
    db_session.expire_all()
    assert db_session.get(Job, job.id).status == JobStatus.RUNNING
