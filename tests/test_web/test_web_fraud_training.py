# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

from openclear.services import security_group_service, user_service
from tests.conftest import TenantFactory


def _login(client, tenant_slug, email, password=TenantFactory.PASSWORD):
    client.get("/ui/login")
    csrf = client.cookies.get("csrf_token")
    client.post(
        "/ui/login", data={"tenant_slug": tenant_slug, "email": email, "password": password, "csrf_token": csrf, "next": "/ui/"}
    )
    return csrf


def _grantee(db_session, tenant, email="fraud-training@example.com"):
    """ml_training_example:write is deliberately excluded from every default security
    group (see auth/permissions.py::_NOT_ADMIN_DEFAULT), so tests need a custom group
    that explicitly grants it — same pattern test_web_wsud.py uses for wsud:sign."""
    group = security_group_service.create_security_group(
        db_session, tenant.id, security_group_service.SecurityGroupInput(name="Fraud Training Grantee", permissions=["ml_training_example:write"])
    )
    user = user_service.create_user_with_membership(
        db_session, tenant.id, email=email, password=TenantFactory.PASSWORD, security_group_id=group.id, customer_id=None,
    )
    db_session.commit()
    return user


def test_fraud_examples_page_requires_permission(client, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="web-fraud-forbidden")
    _login(client, tenant.slug, users["admin"].email)

    resp = client.get("/ui/ml-training/fraud-examples", follow_redirects=False)
    assert resp.status_code == 403


def test_submit_check_raw_entry_creates_example(client, db_session, tenant_factory):
    tenant, account, _users = tenant_factory.make(slug="web-fraud-check-raw")
    grantee = _grantee(db_session, tenant)
    csrf = _login(client, tenant.slug, grantee.email)

    resp = client.post(
        "/ui/ml-training/fraud-examples/check",
        data={
            "csrf_token": csrf, "account_id": str(account.id), "check_number": "F001",
            "presented_amount": "400.00", "presented_date": "2026-02-01", "reason_code": "confirmed fraud",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303

    page = client.get("/ui/ml-training/fraud-examples")
    assert "New label" in page.text
    assert "check" in page.text


def test_submit_ach_raw_entry_creates_example(client, db_session, tenant_factory):
    tenant, account, _users = tenant_factory.make(slug="web-fraud-ach-raw")
    grantee = _grantee(db_session, tenant)
    csrf = _login(client, tenant.slug, grantee.email)

    reasons_page = client.get("/ui/ml-training/fraud-examples")
    assert reasons_page.status_code == 200

    from openclear.services import ach_return_reason_service

    reason = ach_return_reason_service.list_ach_return_reasons(db_session, tenant.id, active_only=True)[0]

    resp = client.post(
        "/ui/ml-training/fraud-examples/ach",
        data={
            "csrf_token": csrf, "account_id": str(account.id), "originator_id": "FRAUDCO",
            "originator_name": "Fraud Co", "amount": "999.00", "transaction_type": "debit",
            "sec_code": "WEB", "trace_number": "WEBTRACE1", "effective_date": "2026-02-01",
            "ach_return_reason_id": str(reason.id),
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303

    page = client.get("/ui/ml-training/fraud-examples")
    assert "ach" in page.text


def test_bulk_upload_check_examples_isolates_bad_row(client, db_session, tenant_factory):
    tenant, account, _users = tenant_factory.make(slug="web-fraud-check-bulk")
    grantee = _grantee(db_session, tenant)
    csrf = _login(client, tenant.slug, grantee.email)

    content = (
        "account_number,check_number,presented_amount,presented_date,reason_code\n"
        f"{account.account_number},F100,300.00,2026-02-01,confirmed fraud\n"
        "unknown-account,F101,100.00,2026-02-01,confirmed fraud\n"
    ).encode()

    resp = client.post(
        "/ui/ml-training/fraud-examples/check/bulk",
        data={"csrf_token": csrf},
        files={"upload_file": ("examples.csv", content, "text/csv")},
    )

    assert resp.status_code == 200
    assert "1 of 2 succeeded" in resp.text
    assert "No account found" in resp.text


def test_retract_example_removes_it_from_future_training(client, db_session, tenant_factory):
    tenant, account, _users = tenant_factory.make(slug="web-fraud-retract")
    grantee = _grantee(db_session, tenant)
    csrf = _login(client, tenant.slug, grantee.email)

    client.post(
        "/ui/ml-training/fraud-examples/check",
        data={
            "csrf_token": csrf, "account_id": str(account.id), "check_number": "F200",
            "presented_amount": "300.00", "presented_date": "2026-02-01", "reason_code": "confirmed fraud",
        },
    )

    from openclear.domain.exception_item import ExceptionItem, ExceptionItemSource

    db_session.expire_all()
    example = (
        db_session.query(ExceptionItem)
        .filter(ExceptionItem.tenant_id == tenant.id, ExceptionItem.source == ExceptionItemSource.TRAINING_BACKFILL)
        .one()
    )

    resp = client.post(f"/ui/ml-training/fraud-examples/{example.id}/retract", data={"csrf_token": csrf}, follow_redirects=False)
    assert resp.status_code == 303

    page = client.get("/ui/ml-training/fraud-examples")
    assert "retracted" in page.text
