# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""A check's return deadline (REGULATORY_PLAN.md RG1; UCC 4-104, 4-301, 4-302): counted from the
day it was presented, in the bank's banking days, and the decision deadline is never later than
the cut-off on the check's return day, whatever the minimum review time or a late file would say."""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from openclear.services import cutoff_service

NY = ZoneInfo("America/New_York")


def _ny(value: datetime) -> datetime:
    """In New York time; SQLite returns UTC without a zone."""
    return (value if value.tzinfo else value.replace(tzinfo=timezone.utc)).astimezone(NY)


@pytest.mark.parametrize("presented, return_day, open_on_holidays", [
    (date(2026, 10, 8), date(2026, 10, 9), False),     # Thursday: Friday
    (date(2026, 10, 9), date(2026, 10, 13), False),    # Friday before Columbus Day: Tuesday
    (date(2026, 10, 9), date(2026, 10, 12), True),     # ...unless the bank opens on it
    (date(2026, 10, 10), date(2026, 10, 14), False),   # Saturday: received Tuesday, returned Wednesday
    (date(2026, 11, 25), date(2026, 11, 27), False),   # Wednesday before Thanksgiving: Friday
    (date(2026, 12, 31), date(2027, 1, 4), False),     # New Year's Eve: the next Monday
    (date(2027, 6, 18), date(2027, 6, 21), False),     # Juneteenth on a Saturday isn't observed by the Fed: Monday
])
def test_the_return_day_counts_banking_days(presented, return_day, open_on_holidays):
    assert cutoff_service.midnight_deadline_day(presented, open_on_federal_holidays=open_on_holidays) == return_day


def test_a_bank_closure_moves_it():
    assert cutoff_service.midnight_deadline_day(date(2026, 10, 8), frozenset({date(2026, 10, 9)})) == date(2026, 10, 13)


def _present(db_session, tenant, account, presented: date):
    from sqlalchemy import select

    from openclear.domain.exception_item import ExceptionItem
    from openclear.domain.paid_item import PaidItemSource
    from openclear.networks.check.ingestion import PaidItemSubmission, ingest_paid_item

    paid = ingest_paid_item(db_session, tenant.id, PaidItemSubmission(
        account_id=account.id, check_number="NOTISSUED", presented_amount=Decimal("10.00"), presented_date=presented,
        source=PaidItemSource.API))
    db_session.commit()
    return db_session.execute(select(ExceptionItem).where(ExceptionItem.source_item_id == paid.id)).scalar_one()


def test_a_late_file_cant_push_the_decision_past_the_return_day(db_session, tenant_factory, monkeypatch):
    """Presented Friday 9 October and arriving at 10:30 with two hours' minimum review: the cut-off
    alone would say Tuesday 11:00 (Columbus Day skipped). The midnight deadline is Tuesday 24:00 too,
    so here it holds; on the Wednesday before Thanksgiving it doesn't, and the deadline is capped."""
    tenant, account, _users = tenant_factory.make(slug="return-deadline")
    tenant.decision_cutoffs, tenant.time_zone, tenant.cutoff_min_review_minutes = {"check": "11:00"}, "America/New_York", 120
    db_session.commit()

    arrived = datetime(2026, 11, 25, 10, 0, tzinfo=NY)  # Wednesday 10:00, two hours' review: the cut-off says Friday 11:00
    import openclear.services.cutoff_service as cs

    real_next = cs.next_cutoff
    monkeypatch.setattr(cs, "next_cutoff", lambda arrived_, *a, **k: real_next(arrived.astimezone(timezone.utc), *a, **k))
    item = _present(db_session, tenant, account, date(2026, 11, 24))  # presented Tuesday: returned by Wednesday 24:00
    assert _ny(item.return_deadline) == datetime(2026, 11, 26, 0, 0, tzinfo=NY)
    assert _ny(item.decision_deadline) == datetime(2026, 11, 25, 11, 0, tzinfo=NY)  # capped at Wednesday's cut-off

    on_time = _present(db_session, tenant, account, date(2026, 11, 25))  # presented Wednesday: returned by Friday 24:00
    assert _ny(on_time.decision_deadline) == datetime(2026, 11, 27, 11, 0, tzinfo=NY)  # the cut-off holds


@pytest.mark.parametrize("arrived, due", [
    (datetime(2026, 11, 25, 14, 0, tzinfo=NY), datetime(2026, 11, 25, 16, 0, tzinfo=NY)),   # after the cut-off: two hours to look
    (datetime(2026, 11, 25, 23, 0, tzinfo=NY), datetime(2026, 11, 26, 0, 0, tzinfo=NY)),    # ...but never past midnight
    (datetime(2026, 11, 26, 9, 0, tzinfo=NY), datetime(2026, 11, 25, 11, 0, tzinfo=NY)),    # midnight has passed: due at once
])
def test_a_check_arriving_after_its_return_days_cutoff_gets_a_look_before_a_default(db_session, tenant_factory, monkeypatch, arrived, due):
    """REVIEW_1_33_PLAN.md G1, RV-D7: presented Tuesday 24 November (returned by Wednesday 24:00),
    reaching OpenClear after Wednesday's 11:00 cut-off."""
    import openclear.services.cutoff_service as cs

    tenant, account, _users = tenant_factory.make(slug="return-late-arrival")
    tenant.decision_cutoffs, tenant.time_zone, tenant.cutoff_min_review_minutes = {"check": "11:00"}, "America/New_York", 120
    db_session.commit()
    monkeypatch.setattr(cs, "_now", lambda: arrived.astimezone(timezone.utc))
    item = _present(db_session, tenant, account, date(2026, 11, 24))
    assert _ny(item.decision_deadline) == due


def test_a_bank_open_on_federal_holidays_has_its_cutoff_on_them():
    """REVIEW_1_33_PLAN.md G6: the setting said holidays count as banking days; the cut-off agrees."""
    arrived = datetime(2026, 10, 12, 8, 0, tzinfo=NY)  # Columbus Day, 08:00
    assert _ny(cutoff_service.next_cutoff(arrived, "11:00", "America/New_York", 120)) == datetime(2026, 10, 13, 11, 0, tzinfo=NY)
    assert _ny(cutoff_service.next_cutoff(arrived, "11:00", "America/New_York", 120, open_on_federal_holidays=True)) == \
        datetime(2026, 10, 12, 11, 0, tzinfo=NY)


def test_a_decision_after_the_return_deadline_is_flagged(db_session, tenant_factory, monkeypatch):
    """In the feed, the CSV, the webhook and a security event, from one answer."""
    from sqlalchemy import select

    from openclear.config import get_settings
    from openclear.domain.decision import DecisionOutcome
    from openclear.domain.security_event import SecurityEvent
    from openclear.domain.webhook import WebhookDelivery
    from openclear.services import decision_feed_service, decision_service, webhook_service
    from tests.test_web.test_web_exception_review import _ctx

    tenant, account, users = tenant_factory.make(slug="return-late")
    tenant.decision_cutoffs = {"check": "11:00"}
    monkeypatch.setattr(get_settings(), "webhook_allow_private_hosts", True)
    webhook_service.create_endpoint(db_session, tenant.id, url="http://127.0.0.1:9/hook", event_types=["exception.decided"])
    db_session.commit()
    item = _present(db_session, tenant, account, date(2026, 10, 1))  # weeks ago: due at once, and late
    result = decision_service.decide(db_session, tenant.id, item.id, _ctx(tenant, users["admin"]), outcome=DecisionOutcome.PAY,
                                     reason_code="Reviewed", notes=None, channel="web")
    assert result.error is None
    db_session.commit()
    feed = decision_feed_service.read_feed(db_session, tenant.id)
    [entry] = feed
    events = db_session.execute(select(SecurityEvent).where(SecurityEvent.name == "decision.after_return_deadline",
                                                            SecurityEvent.tenant_id == tenant.id)).scalars().all()
    assert len(events) == 1 and events[0].severity == "high"
    assert entry["after_return_deadline"] is True
    assert decision_feed_service.to_csv(feed).splitlines()[1].endswith(",True")  # the last column of the OpenClear CSV layout
    [delivery] = db_session.execute(select(WebhookDelivery).where(WebhookDelivery.tenant_id == tenant.id)).scalars().all()
    assert delivery.payload["data"]["after_return_deadline"] is True


def test_the_control_fails_after_a_late_decision_and_warns_on_a_later_customer_cutoff(db_session, tenant_factory):
    from openclear.domain.customer_disposition_setting import CustomerDispositionSetting
    from openclear.domain.decision import DecisionOutcome
    from openclear.services import control_checks, customer_service, decision_service
    from tests.test_web.test_web_exception_review import _ctx

    tenant, account, users = tenant_factory.make(slug="return-control")
    tenant.decision_cutoffs = {"check": "11:00"}
    db_session.commit()
    assert control_checks.return_deadline(db_session, tenant.id).status == "PASS"

    customer = customer_service.create_customer(db_session, tenant.id, customer_service.CustomerInput(customer_number="RD-1", name="Late Co"))
    db_session.add(CustomerDispositionSetting(tenant_id=tenant.id, customer_id=customer.id, network_code="check", cutoff_time="15:00"))
    db_session.commit()
    warned = control_checks.return_deadline(db_session, tenant.id)
    assert warned.status == "WARN" and "Late Co (check 15:00, the bank's 11:00)" in warned.detail

    item = _present(db_session, tenant, account, date(2026, 10, 1))
    decision_service.decide(db_session, tenant.id, item.id, _ctx(tenant, users["admin"]), outcome=DecisionOutcome.PAY, reason_code="Reviewed",
                            notes=None, channel="web")
    db_session.commit()
    assert control_checks.return_deadline(db_session, tenant.id).status == "FAIL"
