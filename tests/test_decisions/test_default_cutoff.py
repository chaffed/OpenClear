# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed
"""Every bank has a decision cut-off (the product owner's decision, 2026-10-21): until it sets its
own, the installation's default, 11:00 New York time. The API marks a decision that isn't final."""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from openclear.config import Settings, assert_production_safe, get_settings
from openclear.networks.check.ingestion import PaidItemSubmission, ingest_paid_item
from openclear.repositories.exception_repo import ExceptionRepository
from openclear.services import cutoff_service, decision_service
from tests.conftest import login_headers

NY = ZoneInfo("America/New_York")


@pytest.fixture
def default_cutoff(monkeypatch):
    monkeypatch.setenv("OPENCLEAR_DEFAULT_DECISION_CUTOFF", "11:00")
    get_settings.cache_clear()


def _presented(db_session, tenant, account, number):
    paid = ingest_paid_item(db_session, tenant.id, PaidItemSubmission(account_id=account.id, check_number=number,
                                                                      presented_amount=Decimal("10.00"), presented_date=date(2026, 10, 1)))
    db_session.commit()
    return ExceptionRepository(db_session, tenant.id).list(source_item_id=paid.id)[0]


def _local(when: datetime) -> str:
    return (when if when.tzinfo else when.replace(tzinfo=timezone.utc)).astimezone(NY).strftime("%H:%M")


def test_a_bank_that_set_nothing_has_the_default(db_session, tenant_factory, default_cutoff):
    tenant, account, _users = tenant_factory.make(slug="default-cutoff")
    item = _presented(db_session, tenant, account, "9101")
    assert item.decision_deadline is not None and _local(item.decision_deadline) == "11:00"
    assert cutoff_service.describe(tenant) == "11:00 (New York, the default)"
    cutoff_service.apply_bank_cutoffs(db_session, tenant.id, time_zone="America/Chicago", cutoffs={"check": "15:00"}, min_review_minutes=120)
    later = _presented(db_session, tenant, account, "9102")
    assert (later.decision_deadline if later.decision_deadline.tzinfo else later.decision_deadline.replace(tzinfo=timezone.utc)
            ).astimezone(ZoneInfo("America/Chicago")).strftime("%H:%M") == "15:00"


def test_production_refuses_to_start_without_a_default():
    with pytest.raises(RuntimeError, match="every bank needs a decision cut-off"):
        assert_production_safe(Settings(environment="production", default_decision_cutoff=""))


def test_the_api_marks_a_decision_that_isnt_final(client, db_session, tenant_factory, default_cutoff):
    tenant, account, users = tenant_factory.make(slug="default-cutoff-api")
    item = _presented(db_session, tenant, account, "9201")
    headers = login_headers(client, tenant.slug, users["admin"].email)
    undecided = client.get(f"/api/v1/exceptions/{item.id}", headers=headers).json()
    assert undecided["decision_final"] is None and undecided["changeable_until"] is None

    client.post(f"/api/v1/exceptions/{item.id}/decide", headers=headers, json={"outcome": "pay", "reason_code": "Reviewed"})
    held = client.get(f"/api/v1/exceptions/{item.id}", headers=headers).json()
    assert held["status"] == "pay" and held["decision_final"] is False and held["changeable_until"] is not None
    listed = next(row for row in client.get("/api/v1/exceptions", headers=headers).json() if row["id"] == str(item.id))
    assert listed["decision_final"] is False

    item.decision_deadline = datetime.now(timezone.utc) - timedelta(minutes=1)
    db_session.commit()
    decision_service.lock_due(db_session)
    db_session.commit()
    final = client.get(f"/api/v1/exceptions/{item.id}", headers=headers).json()
    assert final["decision_final"] is True and final["changeable_until"] is None
