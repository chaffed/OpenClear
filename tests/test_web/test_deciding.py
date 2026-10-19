# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Deciding (MASTER_PLAN.md M3; UX_PLAN.md U4–U6): Pay and Return buttons, a reason only on
Return from the bank's list, several items at once by the same rules as one, and "decide
now" for a customer's people."""

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from openclear.domain.check_return_reason import CheckReturnReason
from openclear.domain.decision import Decision
from openclear.domain.exception_item import ExceptionStatus
from openclear.services import check_return_reason_service, security_group_service, user_service
from tests.conftest import TenantFactory
from tests.test_ml.test_per_customer_ml import _make_customer_with_account, _make_exception


def _login(client, tenant, email):
    client.cookies.clear()
    client.get("/ui/login")
    client.post("/ui/login", data={"tenant_slug": tenant.slug, "email": email, "password": TenantFactory.PASSWORD,
                                   "csrf_token": client.cookies.get("csrf_token"), "next": "/ui/"}, follow_redirects=False)
    return client.cookies.get("csrf_token")


def _reason(db_session, tenant, text):
    return db_session.execute(select(CheckReturnReason).where(CheckReturnReason.tenant_id == tenant.id,
                                                              CheckReturnReason.reason_text == text)).scalar_one()


def test_every_bank_has_check_return_reasons(db_session, tenant_factory):
    tenant, _a, _u = tenant_factory.make(slug="dec-reasons")
    reasons = check_return_reason_service.list_check_return_reasons(db_session, tenant.id)
    assert {(r.reason_text, r.return_code) for r in reasons} == set(check_return_reason_service.DEFAULT_CHECK_RETURN_REASONS)


def test_pay_needs_no_reason_and_return_needs_one_from_the_list(client, db_session, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="dec-buttons")
    paid = _make_exception(db_session, tenant, account, users, "8101", "100.00", "999.00")
    returned = _make_exception(db_session, tenant, account, users, "8102", "100.00", "999.00")
    csrf = _login(client, tenant, users["approver"].email)

    page = client.get(f"/ui/exceptions/{paid.id}").text
    assert "Altered or fictitious item" in page and 'name="return_reason_id"' in page

    assert client.post(f"/ui/exceptions/{paid.id}/decide", data={"csrf_token": csrf, "outcome": "pay"},
                       follow_redirects=False).status_code == 303
    decision = db_session.execute(select(Decision).where(Decision.exception_item_id == paid.id)).scalar_one()
    assert decision.reason_code == "Reviewed and paid"

    missing = client.post(f"/ui/exceptions/{returned.id}/decide", data={"csrf_token": csrf, "outcome": "return"},
                          follow_redirects=False)
    assert "Choose+why" in missing.headers["location"]
    altered = _reason(db_session, tenant, "Altered or fictitious item")
    client.post(f"/ui/exceptions/{returned.id}/decide",
                data={"csrf_token": csrf, "outcome": "return", "return_reason_id": str(altered.id), "notes": "called the vendor"})
    decision = db_session.execute(select(Decision).where(Decision.exception_item_id == returned.id)).scalar_one()
    assert (decision.reason_code, decision.return_transaction_code, decision.notes) == ("Altered or fictitious item", "N", "called the vendor")


def test_several_at_once_follow_the_rules_for_each(client, db_session, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="dec-bulk")
    items = [_make_exception(db_session, tenant, account, users, f"82{n:02d}", "10.00", "99.00") for n in range(4)]
    csrf = _login(client, tenant, users["approver"].email)

    paid = client.post("/ui/exceptions/bulk", data={"csrf_token": csrf, "outcome": "pay",
                                                    "exception_ids": [str(items[0].id), str(items[1].id)]}, follow_redirects=False)
    assert "Paid+2+items" in paid.headers["location"]
    no_reason = client.post("/ui/exceptions/bulk", data={"csrf_token": csrf, "outcome": "return",
                                                         "exception_ids": [str(items[2].id)]}, follow_redirects=False)
    assert "couldn" in no_reason.headers["location"] and "choose+why" in no_reason.headers["location"]
    stop = _reason(db_session, tenant, "Stop payment")
    client.post("/ui/exceptions/bulk", data={"csrf_token": csrf, "outcome": "return", "check_return_reason_id": str(stop.id),
                                             "exception_ids": [str(items[2].id), str(items[3].id), str(items[0].id)]})
    db_session.expire_all()
    statuses = [db_session.get(type(i), i.id).status for i in items]
    assert statuses == [ExceptionStatus.PAY, ExceptionStatus.PAY, ExceptionStatus.RETURN, ExceptionStatus.RETURN]  # 0 stayed paid


def test_several_at_once_under_dual_control(client, db_session, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="dec-bulk-dual", require_dual_control=True)
    items = [_make_exception(db_session, tenant, account, users, f"83{n:02d}", "10.00", "99.00") for n in range(2)]
    ids = [str(i.id) for i in items]
    csrf = _login(client, tenant, users["preparer"].email)
    client.post("/ui/exceptions/bulk", data={"csrf_token": csrf, "outcome": "pay", "exception_ids": ids})
    db_session.expire_all()
    assert all(db_session.get(type(i), i.id).status == ExceptionStatus.PENDING_APPROVAL for i in items)  # recommended, not decided

    csrf = _login(client, tenant, users["approver"].email)
    client.post("/ui/exceptions/bulk", data={"csrf_token": csrf, "outcome": "pay", "exception_ids": ids})
    db_session.expire_all()
    assert all(db_session.get(type(i), i.id).status == ExceptionStatus.PAY for i in items)


def test_decide_now_for_a_customers_people(client, db_session, tenant_factory):
    tenant, _house, users = tenant_factory.make(slug="dec-now")
    customer, account = _make_customer_with_account(db_session, tenant, "DN-1")
    item = _make_exception(db_session, tenant, account, users, "8401", "10.00", "99.00")
    item.decision_deadline = datetime.now(timezone.utc) + timedelta(minutes=45)
    group = security_group_service.get_security_group_by_name(db_session, tenant.id, "Approver")
    clerk = user_service.create_user_with_membership(db_session, tenant.id, email="ap.clerk@dn-1.example.com",
                                                     password=TenantFactory.PASSWORD, security_group_id=group.id, customer_id=customer.id)
    db_session.commit()
    _login(client, tenant, clerk.email)

    landing = client.get("/ui/", follow_redirects=False)
    assert landing.status_code == 303 and landing.headers["location"] == "/ui/exceptions"
    assert "Dashboard" in client.get("/ui/?home=1").text  # the dashboard is still a click away
    page = client.get("/ui/exceptions").text
    assert "decide-banner-soon" in page and "1 item to decide" in page

    # Bank staff aren't sent away from the dashboard, and see the count in the nav instead.
    _login(client, tenant, users["approver"].email)
    assert client.get("/ui/", follow_redirects=False).status_code == 200
    assert "decide-banner" not in client.get("/ui/").text


def test_the_phone_layout_is_there(client, db_session, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="dec-phone")
    _make_exception(db_session, tenant, account, users, "8501", "10.00", "99.00")
    _login(client, tenant, users["approver"].email)
    page = client.get("/ui/exceptions").text
    assert 'class="responsive"' in page and 'data-label="Amount"' in page and 'form="bulk-form"' in page
    assert 'data-row-link' in page and 'data-sheet-open="queue-filters"' in page
    css = client.get("/static/css/app.css").text
    assert ".decision-card { position: sticky;" in css


def test_times_shown_in_the_readers_zone_are_marked_utc():
    """SQLite gives times back without a zone; the browser must still be told they're UTC."""
    from openclear.web.templates import _iso_utc

    assert _iso_utc(datetime(2026, 10, 11, 15, 0)) == "2026-10-11T15:00:00+00:00"
    assert _iso_utc(datetime(2026, 10, 11, 15, 0, tzinfo=timezone(timedelta(hours=-7)))) == "2026-10-11T22:00:00+00:00"
