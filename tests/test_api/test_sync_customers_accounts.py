# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Syncing customers and accounts from a core (SDK_PLAN.md P0): upserts by the bank's own
numbers, safe to repeat, scoped for customer callers."""

from openclear.services import customer_service, security_group_service, user_service
from tests.conftest import TenantFactory, login_headers


def test_customers_upsert_by_number(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="sync-customers")
    headers = login_headers(client, tenant.slug, users["admin"].email)

    created = client.put("/api/v1/customers/CIF-1001", headers=headers, json={"name": "Acme Dental", "external_customer_id": "CORE-77", "city": "Austin"})
    again = client.put("/api/v1/customers/CIF-1001", headers=headers, json={"name": "Acme Dental LLC", "external_customer_id": "CORE-77"})
    assert created.status_code == 201 and again.status_code == 200
    assert again.json()["id"] == created.json()["id"] and again.json()["name"] == "Acme Dental LLC" and again.json()["city"] is None

    off = client.put("/api/v1/customers/CIF-1001", headers=headers, json={"name": "Acme Dental LLC", "is_active": False})
    assert off.json()["is_active"] is False
    listed = client.get("/api/v1/customers?external_customer_id=CORE-77", headers=headers)
    assert listed.headers["x-total-count"] == "0"  # cleared by the last PUT, which left it out
    assert client.get("/api/v1/customers", headers=headers).headers["x-total-count"] == "1"

    viewer = login_headers(client, tenant.slug, users["viewer"].email)
    assert client.put("/api/v1/customers/CIF-2", headers=viewer, json={"name": "X"}).status_code == 403


def test_accounts_upsert_by_number_and_assign_customers(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="sync-accounts")
    headers = login_headers(client, tenant.slug, users["admin"].email)
    client.put("/api/v1/customers/C-1", headers=headers, json={"name": "Acme"})

    created = client.put("/api/v1/accounts/100200300", headers=headers, json={"name": "Operating", "customer_number": "C-1", "ach_debit_block_mode": "block_all"})
    assert created.status_code == 201 and created.json()["ach_debit_block_mode"] == "block_all"
    moved = client.put("/api/v1/accounts/100200300", headers=headers, json={"name": "Operating 2"})
    assert moved.status_code == 200 and moved.json()["customer_id"] is None and moved.json()["ach_debit_block_mode"] == "block_all"
    assert client.put("/api/v1/accounts/9", headers=headers, json={"name": "X", "customer_number": "NOPE"}).status_code == 422
    assert "100200300" in [a["account_number"] for a in client.get("/api/v1/accounts", headers=headers).json()]


def test_a_customer_scoped_caller_only_syncs_its_own(client, db_session, tenant_factory):
    tenant, house, users = tenant_factory.make(slug="sync-scoped")
    mine = customer_service.create_customer(db_session, tenant.id, customer_service.CustomerInput(customer_number="M-1", name="Mine"))
    customer_service.create_customer(db_session, tenant.id, customer_service.CustomerInput(customer_number="O-1", name="Other"))
    group = security_group_service.create_security_group(
        db_session, tenant.id, security_group_service.SecurityGroupInput(name="Account Sync", permissions=["account:read", "account:write"])
    )
    user_service.create_user_with_membership(
        db_session, tenant.id, email="erp@mine.example.com", password=TenantFactory.PASSWORD, security_group_id=group.id, customer_id=mine.id,
    )
    db_session.commit()
    headers = login_headers(client, tenant.slug, "erp@mine.example.com")

    assert client.put("/api/v1/accounts/M-ACCT", headers=headers, json={"name": "Mine", "customer_number": "M-1"}).status_code == 201
    assert client.put("/api/v1/accounts/O-ACCT", headers=headers, json={"name": "Theirs", "customer_number": "O-1"}).status_code == 403
    assert client.put(f"/api/v1/accounts/{house.account_number}", headers=headers, json={"name": "Taken", "customer_number": "M-1"}).status_code == 404
    assert [a["account_number"] for a in client.get("/api/v1/accounts", headers=headers).json()] == ["M-ACCT"]
