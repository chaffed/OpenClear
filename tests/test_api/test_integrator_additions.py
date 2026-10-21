# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""What an integrator needed and couldn't get (FULL_REVIEW_PLAN.md FR6, F5): one customer's items
and decisions, the bank's return reasons, and a decision's earlier versions."""

from datetime import timedelta

from openclear.domain.decision import DecisionOutcome
from openclear.services import decision_service
from tests.conftest import login_headers
from tests.test_decisions.test_changeable import _decide, _item
from tests.test_web.test_web_exception_review import _ctx


def _customer_item(db_session, tenant, account, number, customer_number):
    from openclear.domain.customer import Customer

    customer = Customer(tenant_id=tenant.id, name=f"Customer {customer_number}", customer_number=customer_number)
    db_session.add(customer)
    db_session.flush()
    account.customer_id = customer.id
    db_session.commit()
    item = _item(db_session, tenant, account, number=number, due_in=None)
    return customer, item


def test_exceptions_and_decisions_for_one_customer(client, db_session, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="api-cust-filter")
    _customer, item = _customer_item(db_session, tenant, account, "9101", "C-77")
    from openclear.domain.account import Account

    second_account = Account(tenant_id=tenant.id, account_number="2222333344", name="Other")
    db_session.add(second_account)
    db_session.commit()
    _other, other_item = _customer_item(db_session, tenant, second_account, "9102", "C-88")
    _decide(db_session, tenant, item, users["admin"])
    _decide(db_session, tenant, other_item, users["admin"])
    headers = login_headers(client, tenant.slug, users["admin"].email)

    listed = client.get("/api/v1/exceptions", params={"customer_number": "C-77", "status_filter": "pay"}, headers=headers).json()
    assert [e["id"] for e in listed] == [str(item.id)]
    feed = client.get("/api/v1/decisions", params={"customer_number": "C-77"}, headers=headers).json()
    assert [d["exception_id"] for d in feed] == [str(item.id)]
    assert client.get("/api/v1/exceptions", params={"customer_number": "nobody"}, headers=headers).status_code == 404


def test_the_return_reasons_an_api_client_must_name(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="api-reasons")
    headers = login_headers(client, tenant.slug, users["viewer"].email)
    checks = client.get("/api/v1/return-reasons/check", headers=headers)
    ach = client.get("/api/v1/return-reasons/ach", headers=headers)
    assert checks.status_code == 200 and checks.json() and {"id", "reason_text", "return_code"} <= set(checks.json()[0])
    assert ach.status_code == 200 and all({"id", "reason_text", "transaction_code"} <= set(r) for r in ach.json())


def test_a_check_returned_through_the_api_with_the_banks_reason(client, db_session, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="api-check-reason")
    item = _item(db_session, tenant, account, number="9201", due_in=None)
    headers = login_headers(client, tenant.slug, users["admin"].email)
    reason = client.get("/api/v1/return-reasons/check", headers=headers).json()[0]
    resp = client.post(f"/api/v1/exceptions/{item.id}/decide", json={"outcome": "return", "check_return_reason_id": reason["id"]},
                       headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["reason_code"] == reason["reason_text"]


def test_a_decisions_earlier_versions(client, db_session, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="api-history")
    item = _item(db_session, tenant, account, number="9301", due_in=timedelta(hours=3))
    _decide(db_session, tenant, item, users["admin"])
    decision_service.change_decision(db_session, tenant.id, item.id, _ctx(tenant, users["admin"]), outcome=DecisionOutcome.RETURN,
                                     reason_code="Altered", notes=None)
    db_session.commit()
    history = client.get(f"/api/v1/exceptions/{item.id}/decision/history",
                         headers=login_headers(client, tenant.slug, users["viewer"].email)).json()
    assert [(h["outcome"], h["undone"]) for h in history] == [("pay", False)]
