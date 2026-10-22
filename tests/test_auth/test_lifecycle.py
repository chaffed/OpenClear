# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Statuses that follow the calendar and the stops (FULL_REVIEW_PLAN.md FR5, F1 and F4)."""

from datetime import date, timedelta
from decimal import Decimal

import pytest

from openclear.domain.issued_item import IssuedItemStatus
from openclear.domain.stop_payment import StopPaymentStatus
from openclear.services import issued_item_service, lifecycle_service, stop_payment_service


def _issue(db_session, tenant, account, number, issued_on):
    item = issued_item_service.create_issued_item(db_session, tenant.id, issued_item_service.IssuedItemInput(
        account_id=account.id, check_number=number, amount=Decimal("80.00"), payee_name="Acme", issue_date=issued_on),
        submitted_by_user_id=None)
    db_session.commit()
    return item


def _stop(db_session, tenant, account, number, expires=None, amount=None):
    stop = stop_payment_service.create_stop_payment(db_session, tenant.id, stop_payment_service.StopPaymentInput(
        account_id=account.id, check_number=number, amount=amount, effective_date=date.today(), expiration_date=expires, reason=None),
        created_by_user_id=None)
    db_session.commit()
    return stop


def test_a_stop_marks_its_check_stopped_until_cancelled(db_session, tenant_factory):
    tenant, account, _users = tenant_factory.make(slug="life-stop")
    item = _issue(db_session, tenant, account, "8101", date.today())
    stop = _stop(db_session, tenant, account, "8101")
    assert item.status == IssuedItemStatus.STOPPED
    stop_payment_service.cancel_stop_payment(db_session, tenant.id, stop.id)
    db_session.commit()
    assert item.status == IssuedItemStatus.OUTSTANDING


def test_the_nightly_roll_expires_stops_and_marks_stale_checks(db_session, tenant_factory):
    tenant, account, _users = tenant_factory.make(slug="life-roll")
    stopped = _issue(db_session, tenant, account, "8201", date.today())
    stop = _stop(db_session, tenant, account, "8201", expires=date.today() + timedelta(days=1))
    old = _issue(db_session, tenant, account, "8202", date.today() - timedelta(days=200))
    fresh = _issue(db_session, tenant, account, "8203", date.today() - timedelta(days=10))

    counts = lifecycle_service.roll(db_session, today=date.today() + timedelta(days=2))

    db_session.expire_all()
    assert counts["stops_expired"] >= 1 and counts["checks_stale"] >= 1
    assert stop.status == StopPaymentStatus.EXPIRED
    assert stopped.status == IssuedItemStatus.OUTSTANDING  # its stop ran out
    assert old.status == IssuedItemStatus.STALE
    assert fresh.status == IssuedItemStatus.OUTSTANDING


def test_a_paid_check_cant_be_voided(db_session, tenant_factory):
    tenant, account, _users = tenant_factory.make(slug="life-void")
    item = _issue(db_session, tenant, account, "8301", date.today())
    item.status = IssuedItemStatus.PAID
    db_session.commit()
    with pytest.raises(issued_item_service.CantVoid):
        issued_item_service.void_issued_item(db_session, tenant.id, item.id, "oops")


def test_the_api_refuses_to_void_a_paid_check(client, db_session, tenant_factory):
    from tests.conftest import login_headers

    tenant, account, users = tenant_factory.make(slug="life-void-api")
    item = _issue(db_session, tenant, account, "8302", date.today())
    item.status = IssuedItemStatus.PAID
    db_session.commit()
    resp = client.patch(f"/api/v1/issued-items/{item.id}/void", json={"reason": "oops"},
                        headers=login_headers(client, tenant.slug, users["admin"].email))
    assert resp.status_code == 409
