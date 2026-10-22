# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Reminders before the cutoff (UX_PLAN.md U3; services/reminder_service.py): timing from
the installation, the bank and the customer; one message per person per reminder, covering
every item due; each item reminded once per offset; email and SMS through each person's
preferences."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from openclear.domain.deadline_reminder import DeadlineReminder
from openclear.domain.notification import Notification, NotificationChannel, NotificationPreference, NotificationType
from openclear.domain.tenant import Tenant
from openclear.services import customer_service, reminder_service
from tests.items import _make_exception


def test_parsing_and_describing_offsets():
    assert reminder_service.parse_offsets("120, 30") == [120, 30]
    assert reminder_service.parse_offsets("30, 2h, 2 hours") == [120, 30]
    assert reminder_service.parse_offsets("1.5h; 45m") == [90, 45]
    assert reminder_service.parse_offsets("") == []
    for bad in ("soon", "0", "25h", "1,2,3,4,5"):
        with pytest.raises(reminder_service.ReminderError):
            reminder_service.parse_offsets(bad)
    assert reminder_service.describe([120, 30]) == "2 hours and 30 minutes before the cutoff"
    assert reminder_service.describe([90]) == "1 hour 30 minutes before the cutoff"
    assert reminder_service.describe([]) == "Off"


def _due_in(db_session, exception, minutes, *, customer=None):
    exception.decision_deadline = datetime.now(timezone.utc) + timedelta(minutes=minutes)
    if customer is not None:
        exception.customer_id = customer.id
    db_session.commit()
    return exception


def _reminders(db_session, tenant_id):
    return list(db_session.execute(select(Notification).where(
        Notification.tenant_id == tenant_id, Notification.notification_type == NotificationType.DEADLINE_REMINDER,
    ).order_by(Notification.destination)).scalars())


def test_one_message_per_person_covering_every_item_due_and_only_once(db_session, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="remind-grouped")
    _due_in(db_session, _make_exception(db_session, tenant, account, users, "5001", "100.00", "150.00"), 100)
    _due_in(db_session, _make_exception(db_session, tenant, account, users, "5002", "100.00", "175.00"), 20)

    assert reminder_service.send_due(db_session) == 4  # everyone who can recommend or decide; not the viewer
    sent = _reminders(db_session, tenant.id)
    assert {n.destination for n in sent} == {users[g].email for g in ("admin", "preparer", "approver", "bookkeeper")}
    assert all(n.channel == NotificationChannel.EMAIL for n in sent)
    assert sent[0].subject.startswith("2 items to decide, due in ") and "minutes" in sent[0].subject
    assert "2 items" in sent[0].body and "/ui/exceptions" in sent[0].body
    # Both offsets were crossed for the item due in 20 minutes; one for the other. Logged, not resent.
    assert len(db_session.execute(select(DeadlineReminder)).scalars().all()) == 3
    assert reminder_service.send_due(db_session) == 0


def test_the_next_offset_sends_again(db_session, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="remind-twice")
    exception = _due_in(db_session, _make_exception(db_session, tenant, account, users, "5003", "100.00", "150.00"), 100)
    reminder_service.send_due(db_session)
    first = len(_reminders(db_session, tenant.id))
    reminder_service.send_due(db_session, now=exception.decision_deadline.replace(tzinfo=timezone.utc) - timedelta(minutes=25))
    assert len(_reminders(db_session, tenant.id)) == 2 * first


def test_bank_timing_turning_off_and_a_customers_own(db_session, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="remind-levels")
    customer = customer_service.create_customer(db_session, tenant.id, customer_service.CustomerInput(customer_number="R-1", name="Remind Co"))
    db_session.commit()
    exception = _due_in(db_session, _make_exception(db_session, tenant, account, users, "5004", "100.00", "150.00"), 25, customer=customer)

    reminder_service.set_bank_reminders(db_session, tenant.id, mode="off")
    db_session.commit()
    assert reminder_service.send_due(db_session) == 0

    # The customer's own timing wins over the bank's.
    reminder_service.set_customer_reminders(db_session, tenant.id, customer.id, mode="custom", offsets="10")
    db_session.commit()
    assert reminder_service.send_due(db_session) == 0  # 25 minutes out: not yet
    deadline = exception.decision_deadline.replace(tzinfo=timezone.utc)
    assert reminder_service.send_due(db_session, now=deadline - timedelta(minutes=8)) == 4

    reminder_service.set_customer_reminders(db_session, tenant.id, customer.id, mode="bank")
    reminder_service.set_bank_reminders(db_session, tenant.id, mode="custom", offsets="2h")
    db_session.commit()
    assert db_session.get(Tenant, tenant.id).reminder_offsets_minutes == "120"
    with pytest.raises(reminder_service.ReminderError):
        reminder_service.set_bank_reminders(db_session, tenant.id, mode="custom", offsets="")


def test_sms_follows_each_persons_preference(db_session, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="remind-sms")
    approver = users["approver"]
    approver.phone = "+15555550100"
    db_session.add(NotificationPreference(user_id=approver.id, notification_type=NotificationType.DEADLINE_REMINDER,
                                          email_enabled=False, sms_enabled=True))
    db_session.commit()
    _due_in(db_session, _make_exception(db_session, tenant, account, users, "5005", "100.00", "150.00"), 20)

    reminder_service.send_due(db_session)
    mine = [n for n in _reminders(db_session, tenant.id) if n.recipient_user_id == approver.id]
    assert [n.channel for n in mine] == [NotificationChannel.SMS]
    assert mine[0].destination == "+15555550100" and mine[0].body.startswith("OpenClear: 1 item to decide")


def test_decided_and_overdue_items_are_left_alone(db_session, tenant_factory):
    from openclear.domain.exception_item import ExceptionStatus

    tenant, account, users = tenant_factory.make(slug="remind-skip")
    decided = _due_in(db_session, _make_exception(db_session, tenant, account, users, "5006", "100.00", "150.00"), 20)
    decided.status = ExceptionStatus.PAY
    _due_in(db_session, _make_exception(db_session, tenant, account, users, "5007", "100.00", "150.00"), -5)
    db_session.commit()
    assert reminder_service.send_due(db_session) == 0
