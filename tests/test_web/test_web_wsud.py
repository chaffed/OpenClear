# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

from datetime import date
from decimal import Decimal

from openclear.domain.ach_transaction import (
    AchMatchStatus,
    AchSettlementStatus,
    AchTransaction,
    AchTransactionSource,
    AchTransactionType,
)
from openclear.services import account_service, customer_service, security_group_service, user_service
from tests.conftest import TenantFactory


def _login(client, tenant_slug, email, password=TenantFactory.PASSWORD):
    client.get("/ui/login")
    csrf = client.cookies.get("csrf_token")
    client.post(
        "/ui/login", data={"tenant_slug": tenant_slug, "email": email, "password": password, "csrf_token": csrf, "next": "/ui/"}
    )
    return csrf


def _make_customer_scoped_preparer(db_session, tenant, customer, email="preparer@customer.example.com"):
    group = security_group_service.get_security_group_by_name(db_session, tenant.id, "Preparer")
    user = user_service.create_user_with_membership(
        db_session, tenant.id, email=email, password=TenantFactory.PASSWORD, security_group_id=group.id, customer_id=customer.id
    )
    db_session.commit()
    return user


def _make_customer_with_returned_txn(db_session, tenant, number="C1", sec_code="PPD"):
    customer = customer_service.create_customer(
        db_session, tenant.id, customer_service.CustomerInput(customer_number=number, name=f"Customer {number}")
    )
    account = account_service.create_account(
        db_session, tenant.id, account_service.AccountInput(account_number=f"A-{number}", name="Op", customer_id=customer.id)
    )
    txn = AchTransaction(
        tenant_id=tenant.id, account_id=account.id, customer_id=customer.id,
        originator_id="ORIG", originator_name="Orig Co", amount=Decimal("50.00"),
        transaction_type=AchTransactionType.DEBIT, sec_code=sec_code, trace_number=f"T-{number}",
        effective_date=date(2026, 1, 1), match_status=AchMatchStatus.EXCEPTION,
        settlement_status=AchSettlementStatus.RETURNED, source=AchTransactionSource.API,
    )
    db_session.add(txn)
    db_session.commit()
    return customer, account, txn


def test_bank_wide_session_cannot_reach_wsud_sign_page(client, db_session, tenant_factory):
    """wsud:sign is deliberately excluded from Admin's default grant, so a bank-wide
    admin lacks the permission entirely — to prove the customer-scope gate itself (not
    just the permission gate), grant a tenant-wide (customer_id=None) membership
    wsud:sign explicitly via a custom group, and confirm it's STILL blocked."""
    tenant, _account, _users = tenant_factory.make(slug="web-wsud-bank-wide-blocked")
    group = security_group_service.create_security_group(
        db_session, tenant.id, security_group_service.SecurityGroupInput(name="WSUD Grantee", permissions=["wsud:sign"])
    )
    bank_wide_user = user_service.create_user_with_membership(
        db_session, tenant.id, email="bank-wide-wsud@example.com", password=TenantFactory.PASSWORD,
        security_group_id=group.id, customer_id=None,
    )
    db_session.commit()
    _login(client, tenant.slug, bank_wide_user.email)

    resp = client.get("/ui/wsud", follow_redirects=False)
    assert resp.status_code == 404


def test_customer_scoped_user_without_wsud_permission_is_forbidden(client, db_session, tenant_factory):
    tenant, _account, _users = tenant_factory.make(slug="web-wsud-no-permission")
    customer, _account_row, _txn = _make_customer_with_returned_txn(db_session, tenant)
    group = security_group_service.get_security_group_by_name(db_session, tenant.id, "Viewer")
    viewer = user_service.create_user_with_membership(
        db_session, tenant.id, email="viewer@customer.example.com", password=TenantFactory.PASSWORD,
        security_group_id=group.id, customer_id=customer.id,
    )
    db_session.commit()
    _login(client, tenant.slug, viewer.email)

    resp = client.get("/ui/wsud", follow_redirects=False)
    assert resp.status_code == 403


def test_wsud_page_lists_only_eligible_transactions(client, db_session, tenant_factory):
    tenant, _account, _users = tenant_factory.make(slug="web-wsud-list")
    customer, _account_row, txn = _make_customer_with_returned_txn(db_session, tenant)
    preparer = _make_customer_scoped_preparer(db_session, tenant, customer)
    _login(client, tenant.slug, preparer.email)

    resp = client.get("/ui/wsud")
    assert resp.status_code == 200
    assert str(txn.id) in resp.text
    assert "Orig Co" in resp.text


def test_sign_requires_both_consent_checkboxes(client, db_session, tenant_factory):
    tenant, _account, _users = tenant_factory.make(slug="web-wsud-consent-required")
    customer, _account_row, txn = _make_customer_with_returned_txn(db_session, tenant)
    preparer = _make_customer_scoped_preparer(db_session, tenant, customer)
    csrf = _login(client, tenant.slug, preparer.email)

    resp = client.post(
        "/ui/wsud/sign",
        data={"ach_transaction_ids": [str(txn.id)], "signer_typed_name": "Jane Doe", "csrf_token": csrf},
    )
    assert resp.status_code == 422
    assert "consent" in resp.text.lower()


def test_sign_wsud_end_to_end(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="web-wsud-sign-e2e")
    customer, _account_row, txn = _make_customer_with_returned_txn(db_session, tenant)
    preparer = _make_customer_scoped_preparer(db_session, tenant, customer)
    csrf = _login(client, tenant.slug, preparer.email)

    resp = client.post(
        "/ui/wsud/sign",
        data={
            "ach_transaction_ids": [str(txn.id)],
            "signer_typed_name": "Jane Doe",
            "consent_to_electronic_signing": "true",
            "attest_statement_is_true": "true",
            "csrf_token": csrf,
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert "error" not in resp.headers["location"]

    my_statements = client.get("/ui/wsud")
    assert "Jane Doe" in my_statements.text

    admin_client_csrf = _login(client, tenant.slug, users["admin"].email)
    bank_view = client.get(f"/ui/customers/{customer.id}/wsud")
    assert bank_view.status_code == 200
    assert "Jane Doe" in bank_view.text
    assert "valid" in bank_view.text.lower()
    assert "TAMPERED" not in bank_view.text


def test_bank_wide_view_requires_wsud_read_permission(client, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="web-wsud-bank-view-forbidden")
    _login(client, tenant.slug, users["viewer"].email)

    resp = client.get(f"/ui/customers/{tenant.id}/wsud", follow_redirects=False)
    assert resp.status_code == 403


def test_bank_wide_view_404s_for_unknown_customer(client, tenant_factory):
    import uuid

    tenant, _account, users = tenant_factory.make(slug="web-wsud-bank-view-404")
    _login(client, tenant.slug, users["admin"].email)

    resp = client.get(f"/ui/customers/{uuid.uuid4()}/wsud", follow_redirects=False)
    assert resp.status_code == 404
