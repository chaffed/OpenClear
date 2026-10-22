# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed
"""Decision cut-offs (UI_PLAN.md UI-D13): set bank-wide per network, with a customer's own
negotiated time in its place; an item is due at the first cut-off on a business day at least the
minimum review time after it arrives."""

from datetime import date, datetime, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from openclear.networks.check.ingestion import PaidItemSubmission, ingest_paid_item
from openclear.repositories.exception_repo import ExceptionRepository
from openclear.services import cutoff_service
from tests.items import _make_customer_with_account

ET = ZoneInfo("America/New_York")


def _et(*args) -> datetime:
    return datetime(*args, tzinfo=ET).astimezone(timezone.utc)


def _local(when: datetime) -> str:
    """HH:MM in the bank's zone (SQLite gives back UTC times without their zone)."""
    return (when if when.tzinfo else when.replace(tzinfo=timezone.utc)).astimezone(ET).strftime("%H:%M")


def test_the_federal_reserve_holidays_of_2026():
    # Published by the Federal Reserve: July 4 is a Saturday, so nothing is moved for it.
    assert cutoff_service.federal_reserve_holidays(2026) == frozenset({
        date(2026, 1, 1), date(2026, 1, 19), date(2026, 2, 16), date(2026, 5, 25), date(2026, 6, 19),
        date(2026, 7, 4), date(2026, 9, 7), date(2026, 10, 12), date(2026, 11, 11), date(2026, 11, 26), date(2026, 12, 25)})


def test_a_sunday_holiday_is_kept_on_the_monday():
    assert date(2027, 7, 5) in cutoff_service.federal_reserve_holidays(2027)  # July 4, 2027 is a Sunday
    assert date(2028, 12, 25) in cutoff_service.federal_reserve_holidays(2028)


def test_the_next_cutoff_today_if_theres_time():
    assert cutoff_service.next_cutoff(_et(2026, 10, 20, 8, 0), "11:00", "America/New_York", 120) == _et(2026, 10, 20, 11, 0)


def test_too_late_for_todays_cutoff_moves_to_the_next_business_day():
    # 09:30 + 2 hours' review is after 11:00: Wednesday's.
    assert cutoff_service.next_cutoff(_et(2026, 10, 20, 9, 30), "11:00", "America/New_York", 120) == _et(2026, 10, 21, 11, 0)


def test_weekends_holidays_and_closures_are_skipped():
    friday_late = _et(2026, 10, 9, 16, 0)
    # Saturday, Sunday, then Columbus Day (Oct 12): Tuesday.
    assert cutoff_service.next_cutoff(friday_late, "11:00", "America/New_York", 120) == _et(2026, 10, 13, 11, 0)
    assert cutoff_service.next_cutoff(friday_late, "11:00", "America/New_York", 120,
                                      frozenset({date(2026, 10, 13)})) == _et(2026, 10, 14, 11, 0)


def test_the_local_time_holds_across_daylight_saving():
    # Clocks go back on Nov 1, 2026: 11:00 is 15:00 UTC before and 16:00 UTC after.
    assert cutoff_service.next_cutoff(_et(2026, 10, 30, 8, 0), "11:00", "America/New_York", 0).hour == 15
    assert cutoff_service.next_cutoff(_et(2026, 11, 2, 8, 0), "11:00", "America/New_York", 0).hour == 16


@pytest.mark.parametrize("bad", ["11", "25:00", "11:60", "noon", "1100"])
def test_a_time_must_be_hh_mm(bad):
    with pytest.raises(cutoff_service.CutoffError):
        cutoff_service.parse_time(bad)


def test_an_item_is_due_at_the_customers_own_cutoff_else_the_banks(db_session, tenant_factory):
    tenant, account, _users = tenant_factory.make(slug="cutoff-items")
    customer, customer_account = _make_customer_with_account(db_session, tenant, "CUT-1")

    def presented(account_id, number):
        paid = ingest_paid_item(db_session, tenant.id, PaidItemSubmission(account_id=account_id, check_number=number,
                                                                          presented_amount=Decimal("10.00"), presented_date=date(2026, 10, 1)))
        return ExceptionRepository(db_session, tenant.id).list(source_item_id=paid.id)[0].decision_deadline

    # No cut-off yet: as before, the bank's own item has no deadline.
    assert presented(account.id, "1") is None
    cutoff_service.apply_bank_cutoffs(db_session, tenant.id, time_zone="America/New_York", cutoffs={"check": "11:00", "ach": ""},
                                      min_review_minutes=120)
    bank_due = presented(account.id, "2")
    assert bank_due is not None and _local(bank_due) == "11:00"
    cutoff_service.apply_customer_cutoffs(db_session, tenant.id, customer.id, {"check": "14:30"})
    customer_due = presented(customer_account.id, "3")
    assert _local(customer_due) == "14:30"
    assert cutoff_service.customer_cutoffs(db_session, tenant.id, customer.id) == {"check": "14:30"}
    # Changing a cut-off leaves open items' deadlines alone.
    cutoff_service.apply_bank_cutoffs(db_session, tenant.id, time_zone="America/New_York", cutoffs={"check": "09:00"}, min_review_minutes=120)
    db_session.expire_all()
    assert ExceptionRepository(db_session, tenant.id).list()[1].decision_deadline is not None
    assert _local(bank_due) == "11:00"


def test_the_tile_says_what_is_set(db_session, tenant_factory):
    tenant, _account, _users = tenant_factory.make(slug="cutoff-tile")
    assert cutoff_service.describe(tenant) == "Not set"
    cutoff_service.apply_bank_cutoffs(db_session, tenant.id, time_zone="America/Chicago", cutoffs={"check": "11:00", "ach": "15:00"},
                                      min_review_minutes=60)
    assert cutoff_service.describe(tenant) == "ACH 15:00 · Checks 11:00 (Chicago)"
