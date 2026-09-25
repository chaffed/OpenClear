# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

from openclear.db.session import get_session_factory
from openclear.domain.notification import Notification, NotificationChannel, NotificationStatus, NotificationType
from openclear.services import notification_service
from openclear.workers import tasks


class _FakeEmailProvider:
    name = "fake"

    def __init__(self, *, fail: bool = False):
        self.fail = fail
        self.sent: list[tuple[str, str, str]] = []

    def send(self, *, to, subject, body):
        if self.fail:
            raise RuntimeError("simulated provider failure")
        self.sent.append((to, subject, body))


def _queue_one_email(db_session, tenant, user, notification_type=NotificationType.ACCOUNT_LOCKED):
    if notification_type == NotificationType.ACCOUNT_LOCKED:
        notification_service.notify_account_locked(db_session, user)
    else:
        notification_service.notify_account_unlocked(db_session, user)
    db_session.commit()
    # Filtered by notification_type, not just "most recent" -- SQLite's CURRENT_TIMESTAMP
    # only has 1-second resolution, so two rows queued in the same test can tie on
    # created_at and make an order_by(...desc()).first() lookup pick either one.
    return (
        db_session.query(Notification)
        .filter(Notification.recipient_user_id == user.id, Notification.notification_type == notification_type)
        .order_by(Notification.created_at.desc())
        .first()
    )


def test_dispatch_job_sends_pending_and_marks_sent(monkeypatch, session_factory, tenant_factory, db_session):
    tenant, _account, users = tenant_factory.make(slug="dispatch-sends")
    notification = _queue_one_email(db_session, tenant, users["admin"])
    assert notification.status == NotificationStatus.PENDING

    fake = _FakeEmailProvider()
    monkeypatch.setattr(tasks, "get_email_provider", lambda: fake)
    monkeypatch.setattr(tasks, "get_session_factory", lambda: session_factory)

    tasks.notification_dispatch_job()

    db_session.refresh(notification)
    assert notification.status == NotificationStatus.SENT
    assert notification.sent_at is not None
    assert fake.sent == [(notification.destination, notification.subject, notification.body)]


def test_dispatch_job_retries_on_failure_then_gives_up(monkeypatch, session_factory, tenant_factory, db_session):
    tenant, _account, users = tenant_factory.make(slug="dispatch-retries")
    notification = _queue_one_email(db_session, tenant, users["admin"])

    fake = _FakeEmailProvider(fail=True)
    monkeypatch.setattr(tasks, "get_email_provider", lambda: fake)
    monkeypatch.setattr(tasks, "get_session_factory", lambda: session_factory)

    # Attempts 1 and 2 -- still retried (stays PENDING)
    tasks.notification_dispatch_job()
    db_session.refresh(notification)
    assert notification.status == NotificationStatus.PENDING
    assert notification.attempt_count == 1
    assert notification.error is not None

    tasks.notification_dispatch_job()
    db_session.refresh(notification)
    assert notification.status == NotificationStatus.PENDING
    assert notification.attempt_count == 2

    # Attempt 3 -- exhausts retries, terminally failed, no longer picked up
    tasks.notification_dispatch_job()
    db_session.refresh(notification)
    assert notification.status == NotificationStatus.FAILED
    assert notification.attempt_count == 3

    fake.fail = False
    tasks.notification_dispatch_job()
    db_session.refresh(notification)
    assert notification.status == NotificationStatus.FAILED  # never picked up again once terminal
    assert fake.sent == []


def test_dispatch_job_isolates_one_failure_from_the_rest_of_the_batch(monkeypatch, session_factory, tenant_factory, db_session):
    tenant, _account, users = tenant_factory.make(slug="dispatch-isolation")
    bad = _queue_one_email(db_session, tenant, users["admin"], NotificationType.ACCOUNT_LOCKED)
    good = _queue_one_email(db_session, tenant, users["admin"], NotificationType.ACCOUNT_UNLOCKED)

    class _SelectiveProvider:
        name = "selective"

        def send(self, *, to, subject, body):
            # "unlocked" contains "locked" as a substring -- match the exact subject so
            # this only ever fails the ACCOUNT_LOCKED notification, not both.
            if subject == notification_service._EMAIL_SUBJECTS[NotificationType.ACCOUNT_LOCKED]:
                raise RuntimeError("simulated failure for the locked notification only")

    monkeypatch.setattr(tasks, "get_email_provider", lambda: _SelectiveProvider())
    monkeypatch.setattr(tasks, "get_session_factory", lambda: session_factory)

    tasks.notification_dispatch_job()

    db_session.refresh(bad)
    db_session.refresh(good)
    assert bad.status == NotificationStatus.PENDING
    assert bad.attempt_count == 1
    assert good.status == NotificationStatus.SENT


def test_sms_dispatch_uses_sms_provider(monkeypatch, session_factory, tenant_factory, db_session):
    tenant, _account, users = tenant_factory.make(slug="dispatch-sms")
    user = users["admin"]
    notification_service.set_phone(db_session, user, "+15551234567")
    notification_service.set_preference(db_session, user.id, NotificationType.ACCOUNT_UNLOCKED, email_enabled=False, sms_enabled=True)
    db_session.commit()
    notification_service.notify_account_unlocked(db_session, user)
    db_session.commit()

    notification = (
        db_session.query(Notification)
        .filter(Notification.recipient_user_id == user.id, Notification.channel == NotificationChannel.SMS)
        .one()
    )

    class _FakeSmsProvider:
        name = "fake-sms"

        def __init__(self):
            self.sent = []

        def send(self, *, to, body):
            self.sent.append((to, body))

    fake_sms = _FakeSmsProvider()
    monkeypatch.setattr(tasks, "get_sms_provider", lambda: fake_sms)
    monkeypatch.setattr(tasks, "get_session_factory", lambda: session_factory)

    tasks.notification_dispatch_job()

    db_session.refresh(notification)
    assert notification.status == NotificationStatus.SENT
    assert fake_sms.sent == [(user.phone, notification.body)]
