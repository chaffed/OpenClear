# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""OpenClear down across a cut-off (CONTINUITY_PLAN.md BC-D1): the first sweep after it returns
gives the items due in the gap the bank's minimum review time again instead of applying a
default unseen, tells people, records the gap, and the Controls page shows it for a week. An
item whose midnight deadline has passed stays due at once; a sweep after a normal interval
changes nothing."""

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from openclear.domain.audit_log_entry import AuditLogEntry
from openclear.domain.exception_item import ExceptionStatus
from openclear.domain.notification import Notification, NotificationType
from openclear.domain.scheduled_job_run import ScheduledJobRun
from openclear.domain.security_event import SecurityEvent
from openclear.services import continuity_service, control_checks
from tests.test_decisions.test_changeable import _item


def _last_sweep(db_session, ago: timedelta) -> None:
    db_session.merge(ScheduledJobRun(job_id=continuity_service.SWEEP_JOB_ID, last_started_at=datetime.now(timezone.utc) - ago,
                                     last_finished_at=datetime.now(timezone.utc) - ago, last_ok=True))
    db_session.commit()


def _aware(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def test_items_due_during_an_outage_get_the_review_time_again_and_people_are_told(db_session, tenant_factory, monkeypatch, session_factory):
    from openclear.workers import tasks

    tenant, account, users = tenant_factory.make(slug="gap-bank")
    tenant.cutoff_min_review_minutes = 120
    db_session.commit()
    now = datetime.now(timezone.utc)
    during = _item(db_session, tenant, account, number="7001", due_in=timedelta(hours=-1))      # its cut-off passed while down
    during.return_deadline = now + timedelta(hours=20)
    too_late = _item(db_session, tenant, account, number="7002", due_in=timedelta(hours=-2))    # its midnight deadline passed too
    too_late.return_deadline = now - timedelta(minutes=30)
    before = _item(db_session, tenant, account, number="7003", due_in=timedelta(hours=-5))      # due before the outage: swept as usual
    before.return_deadline = now + timedelta(hours=20)
    db_session.commit()
    _last_sweep(db_session, timedelta(hours=3))

    monkeypatch.setattr(tasks, "get_session_factory", lambda: session_factory)
    tasks.sweep_expired_dispositions_job()
    db_session.expire_all()

    assert during.status == ExceptionStatus.OPEN and during.deadline_moved_reason == "outage"
    assert timedelta(minutes=115) < _aware(during.decision_deadline) - now <= timedelta(minutes=125)
    assert too_late.deadline_moved_reason is None and _aware(too_late.decision_deadline) < now  # due at once, as RG1 says
    assert before.deadline_moved_reason is None and _aware(before.decision_deadline) < now
    [entry] = db_session.execute(select(AuditLogEntry).where(AuditLogEntry.tenant_id == tenant.id,
                                                             AuditLogEntry.action == "continuity.gap")).scalars().all()
    assert "across a cut-off" in entry.summary and entry.metadata_json["moved"] == 1 and entry.metadata_json["items_due_in_gap"] == 2
    assert db_session.execute(select(SecurityEvent).where(SecurityEvent.name == "continuity.gap", SecurityEvent.tenant_id == tenant.id)
                              ).scalars().first().severity == "high"
    told = db_session.execute(select(Notification).where(Notification.tenant_id == tenant.id,
                                                         Notification.notification_type == NotificationType.CONTINUITY_GAP)).scalars().all()
    assert told and all("due again" in n.body for n in told) and any(n.recipient_user_id == users["admin"].id for n in told)
    check = control_checks.continuity_gaps(db_session, tenant.id)
    assert check.status == "FAIL" and "across a cut-off" in check.detail


def test_a_sweep_after_a_normal_interval_moves_nothing(db_session, tenant_factory):
    tenant, account, _users = tenant_factory.make(slug="gap-none")
    item = _item(db_session, tenant, account, number="7101", due_in=timedelta(minutes=-1))
    _last_sweep(db_session, timedelta(minutes=5))
    assert continuity_service.recover_from_gap(db_session) == []
    db_session.expire_all()
    assert item.deadline_moved_reason is None and _aware(item.decision_deadline) < datetime.now(timezone.utc)
    assert control_checks.continuity_gaps(db_session, tenant.id).status == "PASS"
    assert continuity_service.gap_before(db_session, datetime.now(timezone.utc)) is None


def test_the_first_sweep_ever_isnt_a_gap(db_session, tenant_factory):
    tenant, account, _users = tenant_factory.make(slug="gap-first")
    _item(db_session, tenant, account, number="7201", due_in=timedelta(minutes=-1))
    assert continuity_service.recover_from_gap(db_session) == []
