# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Reverse positive pay (REVERSE_POSITIVE_PAY_PLAN.md RPP1): on an account in reverse mode
every presented check is an item for the customer to review, a real problem still shows as
itself, positive pay accounts are unchanged, and turning reverse on is a protected change."""

import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

from openclear.domain.account import Account, CheckReviewMode
from openclear.networks.check.rules import evaluate_check_rules
from openclear.networks.check.types import CheckExceptionType as T
from tests.conftest import TenantFactory, login_headers
from tests.test_networks.check.test_rules import ISSUED, base_inputs

VOIDED = type(ISSUED)(id=uuid.uuid4(), amount=Decimal("100.00"), payee_name="ACME", issue_date=date(2026, 1, 1), status="voided")


@pytest.mark.parametrize("overrides, reverse, positive", [
    # A check that isn't in any issued file: the usual case for a reverse customer.
    ({"candidate_issued_item": None}, [T.REVIEW], [T.NOT_IN_FILE]),
    # Issued and matching (a customer half-way through moving): still the customer's to review.
    ({}, [T.REVIEW], []),
    # A real problem shows as itself, never as only "review".
    ({"active_stop_found": True}, [T.STOPPED], [T.STOPPED]),
    ({"candidate_issued_item": None, "is_duplicate_paid": True}, [T.DUPLICATE_PAID, T.REVIEW], [T.DUPLICATE_PAID, T.NOT_IN_FILE]),
    ({"presented_amount": Decimal("150.00")}, [T.AMOUNT_MISMATCH], [T.AMOUNT_MISMATCH]),
    ({"candidate_issued_item": VOIDED}, [T.VOIDED], [T.VOIDED]),
])
def test_a_reverse_account_reviews_every_check(overrides, reverse, positive):
    assert evaluate_check_rules(base_inputs(**overrides, review_all=True))[0] == reverse
    assert evaluate_check_rules(base_inputs(**overrides))[0] == positive


def _login(client, slug, email):
    client.cookies.clear()
    client.get("/ui/login")
    csrf = client.cookies.get("csrf_token")
    client.post("/ui/login", data={"tenant_slug": slug, "email": email, "password": TenantFactory.PASSWORD, "csrf_token": csrf, "next": "/ui/"})
    return csrf


def test_a_check_presented_on_a_reverse_account_is_an_item_to_review(db_session, tenant_factory):
    from openclear.domain.exception_item import ExceptionItem
    from openclear.domain.paid_item import PaidItemSource
    from openclear.networks.check.ingestion import PaidItemSubmission, ingest_paid_item

    tenant, account, _users = tenant_factory.make(slug="rpp-ingest")
    account.check_review_mode = CheckReviewMode.REVERSE
    db_session.commit()
    paid = ingest_paid_item(db_session, tenant.id, PaidItemSubmission(
        account_id=account.id, check_number="9001", presented_amount=Decimal("42.00"), presented_date=date(2026, 10, 21),
        source=PaidItemSource.API))
    db_session.commit()
    item = db_session.execute(select(ExceptionItem).where(ExceptionItem.source_item_id == paid.id)).scalar_one()
    assert item.exception_types == "review" and item.status.value == "open"


def test_turning_reverse_on_waits_for_a_second_admin_and_off_doesnt(client, db_session, tenant_factory):
    from openclear.domain.change_request import ChangeRequest
    from openclear.domain.tenant import Tenant
    from tests.test_auth.test_dual_control import _second_admin

    tenant, account, users = tenant_factory.make(slug="rpp-protected")
    _second_admin(db_session, tenant)
    db_session.get(Tenant, tenant.id).sensitive_changes_need_approval = True
    db_session.commit()
    csrf = _login(client, tenant.slug, users["admin"].email)
    page = client.get(f"/ui/accounts/{account.id}/check-review")
    assert page.status_code == 200 and "This account is on <strong>positive pay</strong>" in page.text

    asked = client.post(f"/ui/accounts/{account.id}/check-review", data={"csrf_token": csrf, "mode": "reverse"}, follow_redirects=False)
    assert "approv" in asked.headers["location"].lower()
    db_session.expire_all()
    assert db_session.get(Account, account.id).check_review_mode == CheckReviewMode.POSITIVE
    change = db_session.execute(select(ChangeRequest).where(ChangeRequest.kind == "account.check_review_mode")).scalar_one()
    assert change.status == "pending"

    db_session.get(Account, account.id).check_review_mode = CheckReviewMode.REVERSE
    db_session.commit()
    client.post(f"/ui/accounts/{account.id}/check-review", data={"csrf_token": csrf, "mode": "positive"})
    db_session.expire_all()
    assert db_session.get(Account, account.id).check_review_mode == CheckReviewMode.POSITIVE  # tightening is at once


def test_only_the_banks_administrators_set_it(client, db_session, tenant_factory):
    from openclear.services import customer_service, security_group_service, user_service

    tenant, account, users = tenant_factory.make(slug="rpp-who")
    customer = customer_service.create_customer(db_session, tenant.id, customer_service.CustomerInput(customer_number="R-1", name="Rev Co"))
    account.customer_id = customer.id
    group = security_group_service.get_security_group_by_name(db_session, tenant.id, "Admin")
    user_service.create_user_with_membership(db_session, tenant.id, email="admin@revco.example", password=TenantFactory.PASSWORD,
                                             security_group_id=group.id, customer_id=customer.id)
    db_session.commit()

    _login(client, tenant.slug, users["preparer"].email)
    assert client.get(f"/ui/accounts/{account.id}/check-review", follow_redirects=False).status_code == 403
    _login(client, tenant.slug, "admin@revco.example")
    assert client.get(f"/ui/accounts/{account.id}/check-review", follow_redirects=False).status_code in (403, 404)

    customer_headers = login_headers(client, tenant.slug, "admin@revco.example")
    body = {"name": account.name, "customer_number": "R-1", "check_review_mode": "reverse"}
    assert client.put(f"/api/v1/accounts/{account.account_number}", headers=customer_headers, json=body).status_code == 403

    # The bank's admin: applied at once (the bank doesn't ask for a second admin), and audited.
    admin = login_headers(client, tenant.slug, users["admin"].email)
    resp = client.put(f"/api/v1/accounts/{account.account_number}", headers=admin, json=body)
    assert resp.status_code == 200 and resp.json()["check_review_mode"] == "reverse"
    from openclear.domain.audit_log_entry import AuditLogEntry

    assert db_session.execute(select(AuditLogEntry).where(AuditLogEntry.action == "account.check_review_mode",
                                                          AuditLogEntry.tenant_id == tenant.id)).scalars().first() is not None
    _login(client, tenant.slug, users["admin"].email)
    assert f'/ui/accounts/{account.id}/check-review">Reverse positive pay</a>' in client.get(f"/ui/customers/{customer.id}").text


def test_the_api_answers_202_while_reverse_waits(client, db_session, tenant_factory):
    from openclear.domain.tenant import Tenant
    from tests.test_auth.test_dual_control import _second_admin

    tenant, account, users = tenant_factory.make(slug="rpp-api-wait")
    _second_admin(db_session, tenant)
    db_session.get(Tenant, tenant.id).sensitive_changes_need_approval = True
    db_session.commit()
    admin = login_headers(client, tenant.slug, users["admin"].email)
    resp = client.put(f"/api/v1/accounts/{account.account_number}", headers=admin,
                      json={"name": "Renamed", "check_review_mode": "reverse"})
    assert resp.status_code == 202 and resp.json()["status"] == "pending"
    db_session.expire_all()
    saved = db_session.get(Account, account.id)
    assert (saved.name, saved.check_review_mode) == ("Renamed", CheckReviewMode.POSITIVE)
