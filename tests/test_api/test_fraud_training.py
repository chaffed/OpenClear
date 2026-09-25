# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

from openclear.services import security_group_service, user_service
from tests.conftest import TenantFactory, login_headers


def _grantee(db_session, tenant, email="fraud-training-api@example.com"):
    group = security_group_service.create_security_group(
        db_session, tenant.id, security_group_service.SecurityGroupInput(name="Fraud Training Grantee", permissions=["ml_training_example:write"])
    )
    user = user_service.create_user_with_membership(
        db_session, tenant.id, email=email, password=TenantFactory.PASSWORD, security_group_id=group.id, customer_id=None,
    )
    db_session.commit()
    return user


def test_create_check_fraud_example_forbidden_without_permission(client, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="api-fraud-forbidden")
    headers = login_headers(client, tenant.slug, users["admin"].email)

    resp = client.post(
        "/api/v1/ml-training/fraud-examples/check",
        headers=headers,
        json={
            "account_id": str(account.id), "check_number": "A001", "presented_amount": "400.00",
            "presented_date": "2026-02-01", "reason_code": "confirmed fraud",
        },
    )
    assert resp.status_code == 403


def test_create_check_fraud_example_raw_entry(client, db_session, tenant_factory):
    tenant, account, _users = tenant_factory.make(slug="api-fraud-check-raw")
    grantee = _grantee(db_session, tenant)
    headers = login_headers(client, tenant.slug, grantee.email)

    resp = client.post(
        "/api/v1/ml-training/fraud-examples/check",
        headers=headers,
        json={
            "account_id": str(account.id), "check_number": "A002", "presented_amount": "400.00",
            "presented_date": "2026-02-01", "reason_code": "confirmed fraud",
        },
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["network_code"] == "check"
    assert body["is_correction"] is False
    assert body["retracted_at"] is None


def test_create_check_fraud_example_attaches_to_existing_paid_item(client, db_session, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="api-fraud-check-attach")
    grantee = _grantee(db_session, tenant)
    admin_headers = login_headers(client, tenant.slug, users["admin"].email)

    issued = client.post(
        "/api/v1/issued-items",
        headers=admin_headers,
        json={"account_id": str(account.id), "check_number": "A003", "amount": "100.00", "payee_name": "X", "issue_date": "2026-01-01"},
    ).json()
    assert issued["check_number"] == "A003"

    paid = client.post(
        "/api/v1/paid-items",
        headers=admin_headers,
        json={"account_id": str(account.id), "check_number": "A003", "presented_amount": "100.00", "presented_date": "2026-01-01"},
    ).json()
    assert paid["match_status"] == "matched"

    headers = login_headers(client, tenant.slug, grantee.email)
    resp = client.post(
        "/api/v1/ml-training/fraud-examples/check",
        headers=headers,
        json={"paid_item_id": paid["id"], "reason_code": "cleared clean, confirmed fraud later"},
    )
    assert resp.status_code == 201


def test_create_check_fraud_example_missing_raw_fields_rejected(client, db_session, tenant_factory):
    tenant, _account, _users = tenant_factory.make(slug="api-fraud-check-missing")
    grantee = _grantee(db_session, tenant)
    headers = login_headers(client, tenant.slug, grantee.email)

    resp = client.post(
        "/api/v1/ml-training/fraud-examples/check",
        headers=headers,
        json={"reason_code": "confirmed fraud"},
    )
    assert resp.status_code == 422


def test_create_ach_fraud_example_invalid_return_reason_rejected(client, db_session, tenant_factory):
    import uuid

    tenant, account, _users = tenant_factory.make(slug="api-fraud-ach-bad-reason")
    grantee = _grantee(db_session, tenant)
    headers = login_headers(client, tenant.slug, grantee.email)

    resp = client.post(
        "/api/v1/ml-training/fraud-examples/ach",
        headers=headers,
        json={
            "account_id": str(account.id), "originator_id": "FRAUDCO", "originator_name": "Fraud Co",
            "amount": "999.00", "transaction_type": "debit", "sec_code": "WEB", "trace_number": "APITRACE1",
            "effective_date": "2026-02-01", "ach_return_reason_id": str(uuid.uuid4()),
        },
    )
    assert resp.status_code == 400


def test_bulk_create_check_fraud_examples_isolates_bad_row(client, db_session, tenant_factory):
    tenant, account, _users = tenant_factory.make(slug="api-fraud-check-bulk")
    grantee = _grantee(db_session, tenant)
    headers = login_headers(client, tenant.slug, grantee.email)

    resp = client.post(
        "/api/v1/ml-training/fraud-examples/check/bulk",
        headers=headers,
        json=[
            {"account_id": str(account.id), "check_number": "A004", "presented_amount": "400.00", "presented_date": "2026-02-01", "reason_code": "x"},
            {"reason_code": "x"},
        ],
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["succeeded"] == 1
    assert body["failed"] == 1


def test_retract_via_api(client, db_session, tenant_factory):
    tenant, account, _users = tenant_factory.make(slug="api-fraud-retract")
    grantee = _grantee(db_session, tenant)
    headers = login_headers(client, tenant.slug, grantee.email)

    created = client.post(
        "/api/v1/ml-training/fraud-examples/check",
        headers=headers,
        json={"account_id": str(account.id), "check_number": "A005", "presented_amount": "400.00", "presented_date": "2026-02-01", "reason_code": "x"},
    ).json()

    resp = client.post(f"/api/v1/ml-training/fraud-examples/{created['id']}/retract", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["retracted_at"] is not None

    resp2 = client.post(f"/api/v1/ml-training/fraud-examples/{created['id']}/retract", headers=headers)
    assert resp2.status_code == 400
