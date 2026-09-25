# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Deliberately attempts to leak every customer-scoped resource type across customers
within the SAME tenant, and asserts each attempt is blocked — the customer-scoping
analogue of test_cross_tenant_isolation.py. This is the primary evidence for the riskiest
part of the customers feature: every repository call site touching a customer-scoped
table (account, issued_item, stop_payment, paid_item, ach_authorization_rule,
ach_transaction, exception_item, check_image) must pass ctx.customer_id, or this leaks."""

from openclear.services import account_service, customer_service, security_group_service, user_service
from tests.conftest import TenantFactory, login_headers


def _two_customers(db_session, tenant_factory):
    tenant, _house_account, users = tenant_factory.make(slug="xc")
    customer_a = customer_service.create_customer(
        db_session, tenant.id, customer_service.CustomerInput(customer_number="C-A", name="Customer A")
    )
    customer_b = customer_service.create_customer(
        db_session, tenant.id, customer_service.CustomerInput(customer_number="C-B", name="Customer B")
    )
    account_a = account_service.create_account(
        db_session, tenant.id, account_service.AccountInput(account_number="A-1", name="A Account", customer_id=customer_a.id)
    )
    account_b = account_service.create_account(
        db_session, tenant.id, account_service.AccountInput(account_number="B-1", name="B Account", customer_id=customer_b.id)
    )
    preparer_group = security_group_service.get_security_group_by_name(db_session, tenant.id, "Preparer")
    user_a = user_service.create_user_with_membership(
        db_session, tenant.id, email="staff-a@xc.example.com", password=TenantFactory.PASSWORD,
        security_group_id=preparer_group.id, customer_id=customer_a.id,
    )
    user_b = user_service.create_user_with_membership(
        db_session, tenant.id, email="staff-b@xc.example.com", password=TenantFactory.PASSWORD,
        security_group_id=preparer_group.id, customer_id=customer_b.id,
    )
    db_session.commit()
    return tenant, users, customer_a, customer_b, account_a, account_b, user_a, user_b


def test_accounts_not_visible_across_customers(db_session, tenant_factory):
    tenant, _users, _customer_a, _customer_b, account_a, account_b, _user_a, _user_b = _two_customers(db_session, tenant_factory)

    # bank-wide (no customer scope) sees everything, including the tenant's house account
    assert {a.account_number for a in account_service.list_accounts(db_session, tenant.id)} == {"0001", "A-1", "B-1"}
    # customer-A-scoped session sees only its own account
    a_scope_accounts = account_service.list_accounts(db_session, tenant.id, customer_id=account_a.customer_id)
    assert {a.account_number for a in a_scope_accounts} == {"A-1"}
    b_scope_accounts = account_service.list_accounts(db_session, tenant.id, customer_id=account_b.customer_id)
    assert {a.account_number for a in b_scope_accounts} == {"B-1"}


def test_issued_items_not_visible_or_voidable_across_customers(client, db_session, tenant_factory):
    tenant, _users, _ca, _cb, account_a, account_b, user_a, user_b = _two_customers(db_session, tenant_factory)
    headers_a = login_headers(client, tenant.slug, user_a.email)
    headers_b = login_headers(client, tenant.slug, user_b.email)

    created = client.post(
        "/api/v1/issued-items",
        headers=headers_a,
        json={
            "account_id": str(account_a.id), "check_number": "1", "amount": "10.00", "payee_name": "X", "issue_date": "2026-01-01",
        },
    ).json()

    assert client.get(f"/api/v1/issued-items/{created['id']}", headers=headers_b).status_code == 404
    assert created["id"] not in [i["id"] for i in client.get("/api/v1/issued-items", headers=headers_b).json()]
    assert (
        client.patch(f"/api/v1/issued-items/{created['id']}/void", headers=headers_b, json={"reason": "x"}).status_code == 404
    )

    # B's staff can't even create an issued item against A's account directly
    resp = client.post(
        "/api/v1/issued-items",
        headers=headers_b,
        json={
            "account_id": str(account_a.id), "check_number": "2", "amount": "5.00", "payee_name": "Y", "issue_date": "2026-01-01",
        },
    )
    assert resp.status_code == 404


def test_stop_payments_not_visible_or_cancelable_across_customers(client, db_session, tenant_factory):
    tenant, _users, _ca, _cb, account_a, _account_b, user_a, user_b = _two_customers(db_session, tenant_factory)
    headers_a = login_headers(client, tenant.slug, user_a.email)
    headers_b = login_headers(client, tenant.slug, user_b.email)

    created = client.post(
        "/api/v1/stop-payments",
        headers=headers_a,
        json={"account_id": str(account_a.id), "check_number": "1", "effective_date": "2026-01-01"},
    ).json()

    assert created["id"] not in [s["id"] for s in client.get("/api/v1/stop-payments", headers=headers_b).json()]
    assert client.patch(f"/api/v1/stop-payments/{created['id']}/cancel", headers=headers_b).status_code == 404


def test_paid_items_not_visible_across_customers(client, db_session, tenant_factory):
    tenant, _users, _ca, _cb, account_a, _account_b, user_a, user_b = _two_customers(db_session, tenant_factory)
    headers_a = login_headers(client, tenant.slug, user_a.email)
    headers_b = login_headers(client, tenant.slug, user_b.email)

    created = client.post(
        "/api/v1/paid-items",
        headers=headers_a,
        json={"account_id": str(account_a.id), "check_number": "1", "presented_amount": "10.00", "presented_date": "2026-01-01"},
    ).json()

    assert client.get(f"/api/v1/paid-items/{created['id']}", headers=headers_b).status_code == 404
    assert created["id"] not in [p["id"] for p in client.get("/api/v1/paid-items", headers=headers_b).json()]


def test_ach_authorizations_and_transactions_not_visible_across_customers(client, db_session, tenant_factory):
    tenant, _users, _ca, _cb, account_a, _account_b, user_a, user_b = _two_customers(db_session, tenant_factory)
    headers_a = login_headers(client, tenant.slug, user_a.email)
    headers_b = login_headers(client, tenant.slug, user_b.email)

    auth = client.post(
        "/api/v1/ach/authorizations",
        headers=headers_a,
        json={"account_id": str(account_a.id), "originator_id": "ORIG", "originator_name": "X", "effective_date": "2026-01-01"},
    ).json()
    assert auth["id"] not in [a["id"] for a in client.get("/api/v1/ach/authorizations", headers=headers_b).json()]
    assert client.patch(f"/api/v1/ach/authorizations/{auth['id']}/revoke", headers=headers_b).status_code == 404

    txn = client.post(
        "/api/v1/ach/transactions",
        headers=headers_a,
        json={
            "account_id": str(account_a.id), "originator_id": "ORIG", "originator_name": "X", "amount": "10.00",
            "transaction_type": "credit", "sec_code": "PPD", "trace_number": "T1", "effective_date": "2026-01-01",
        },
    ).json()
    assert client.get(f"/api/v1/ach/transactions/{txn['id']}", headers=headers_b).status_code == 404


def test_bank_wide_admin_sees_both_customers(client, db_session, tenant_factory):
    tenant, users, _ca, _cb, account_a, account_b, user_a, user_b = _two_customers(db_session, tenant_factory)
    headers_a = login_headers(client, tenant.slug, user_a.email)
    headers_b = login_headers(client, tenant.slug, user_b.email)
    headers_admin = login_headers(client, tenant.slug, users["admin"].email)

    item_a = client.post(
        "/api/v1/issued-items",
        headers=headers_a,
        json={"account_id": str(account_a.id), "check_number": "1", "amount": "10.00", "payee_name": "X", "issue_date": "2026-01-01"},
    ).json()
    item_b = client.post(
        "/api/v1/issued-items",
        headers=headers_b,
        json={"account_id": str(account_b.id), "check_number": "1", "amount": "10.00", "payee_name": "Y", "issue_date": "2026-01-01"},
    ).json()

    admin_ids = {i["id"] for i in client.get("/api/v1/issued-items", headers=headers_admin).json()}
    assert {item_a["id"], item_b["id"]} <= admin_ids
    assert client.get(f"/api/v1/issued-items/{item_a['id']}", headers=headers_admin).status_code == 200
    assert client.get(f"/api/v1/issued-items/{item_b['id']}", headers=headers_admin).status_code == 200


def test_exceptions_not_visible_or_actionable_across_customers(client, db_session, tenant_factory):
    """exception_item/decision were not customer-scoped at all until this test was
    added — see repositories/exception_repo.py (now a CustomerScopedRepository) and
    exception_item.customer_id (denormalized from the source item, same as every other
    customer-scoped table)."""
    tenant, _users, customer_a, customer_b, account_a, _account_b, user_a, user_b = _two_customers(db_session, tenant_factory)
    headers_a = login_headers(client, tenant.slug, user_a.email)
    headers_b = login_headers(client, tenant.slug, user_b.email)

    # No prior issued item for this check number — presenting it creates an exception.
    client.post(
        "/api/v1/paid-items",
        headers=headers_a,
        json={"account_id": str(account_a.id), "check_number": "1", "presented_amount": "10.00", "presented_date": "2026-01-01"},
    )
    exception_a = client.get("/api/v1/exceptions", headers=headers_a).json()[0]
    exception_id = exception_a["id"]

    assert exception_id not in [e["id"] for e in client.get("/api/v1/exceptions", headers=headers_b).json()]
    assert client.get(f"/api/v1/exceptions/{exception_id}", headers=headers_b).status_code == 404
    assert (
        client.post(
            f"/api/v1/exceptions/{exception_id}/recommend",
            headers=headers_b,
            json={"outcome": "pay", "reason_code": "test"},
        ).status_code
        == 404
    )

    # Preparer (user_a/user_b's group) doesn't have exception:decide — use dedicated
    # Approver-group, customer-scoped users so this actually tests the customer-scoping
    # gate rather than the pre-existing permission gate.
    approver_group = security_group_service.get_security_group_by_name(db_session, tenant.id, "Approver")
    approver_a = user_service.create_user_with_membership(
        db_session, tenant.id, email="approver-a@xc.example.com", password=TenantFactory.PASSWORD,
        security_group_id=approver_group.id, customer_id=customer_a.id,
    )
    approver_b = user_service.create_user_with_membership(
        db_session, tenant.id, email="approver-b@xc.example.com", password=TenantFactory.PASSWORD,
        security_group_id=approver_group.id, customer_id=customer_b.id,
    )
    db_session.commit()
    headers_approver_a = login_headers(client, tenant.slug, approver_a.email)
    headers_approver_b = login_headers(client, tenant.slug, approver_b.email)

    assert (
        client.post(
            f"/api/v1/exceptions/{exception_id}/decide",
            headers=headers_approver_b,
            json={"outcome": "pay", "reason_code": "test"},
        ).status_code
        == 404
    )

    # A's own staff CAN decide it — and once decided, B still can't read the decision.
    decide_resp = client.post(
        f"/api/v1/exceptions/{exception_id}/decide", headers=headers_approver_a, json={"outcome": "pay", "reason_code": "test"}
    )
    assert decide_resp.status_code == 200
    assert client.get(f"/api/v1/exceptions/{exception_id}/decision", headers=headers_approver_a).status_code == 200
    assert client.get(f"/api/v1/exceptions/{exception_id}/decision", headers=headers_approver_b).status_code == 404


def test_check_images_not_visible_across_customers(client, db_session, tenant_factory):
    """check_image was not customer-scoped at all until this test was added — see
    repositories/check_image_repo.py (now a CustomerScopedRepository) and
    check_image.customer_id (denormalized from the resolved account/paid item, same as
    every other customer-scoped table)."""
    from openclear.networks.check.ocr_processing import create_check_image

    tenant, _users, customer_a, _customer_b, _account_a, _account_b, user_a, user_b = _two_customers(db_session, tenant_factory)
    image_a = create_check_image(
        db_session, tenant.id, front_bytes=b"front-a-bytes", back_bytes=None, paid_item_id=None, customer_id=customer_a.id
    )
    db_session.commit()

    headers_a = login_headers(client, tenant.slug, user_a.email)
    headers_b = login_headers(client, tenant.slug, user_b.email)

    assert client.get(f"/api/v1/check-images/{image_a.id}", headers=headers_a).status_code == 200
    assert client.get(f"/api/v1/check-images/{image_a.id}", headers=headers_b).status_code == 404
    assert client.post(f"/api/v1/check-images/{image_a.id}/reprocess", headers=headers_b).status_code == 404


def test_check_image_upload_inherits_customer_from_linked_paid_item(client, db_session, tenant_factory):
    tenant, _users, _customer_a, customer_b, account_a, _account_b, user_a, user_b = _two_customers(db_session, tenant_factory)
    headers_a = login_headers(client, tenant.slug, user_a.email)
    headers_b = login_headers(client, tenant.slug, user_b.email)

    paid_item_a = client.post(
        "/api/v1/paid-items",
        headers=headers_a,
        json={"account_id": str(account_a.id), "check_number": "1", "presented_amount": "10.00", "presented_date": "2026-01-01"},
    ).json()

    # B can't even reference A's paid item to attach an image to it
    resp = client.post(
        "/api/v1/check-images",
        headers=headers_b,
        files={"front_image": ("front.png", b"fake-bytes", "image/png")},
        data={"paid_item_id": paid_item_a["id"]},
    )
    assert resp.status_code == 404

    created = client.post(
        "/api/v1/check-images",
        headers=headers_a,
        files={"front_image": ("front.png", b"fake-bytes", "image/png")},
        data={"paid_item_id": paid_item_a["id"]},
    ).json()
    assert client.get(f"/api/v1/check-images/{created['id']}", headers=headers_a).status_code == 200
    assert client.get(f"/api/v1/check-images/{created['id']}", headers=headers_b).status_code == 404


def test_customer_scoped_admin_group_is_still_masked_from_tenant_admin_actions(client, db_session, tenant_factory):
    """A customer-scoped membership can never reach tenant-admin-only actions even if its
    security group nominally contains them (the full "Admin" group here) —
    auth/permissions.py::CUSTOMER_SCOPE_MASKED_PERMISSIONS."""
    tenant, _users, customer_a, _cb, _account_a, _account_b, _user_a, _user_b = _two_customers(db_session, tenant_factory)
    admin_group = security_group_service.get_security_group_by_name(db_session, tenant.id, "Admin")
    scoped_admin = user_service.create_user_with_membership(
        db_session, tenant.id, email="scoped-admin@xc.example.com", password=TenantFactory.PASSWORD,
        security_group_id=admin_group.id, customer_id=customer_a.id,
    )
    db_session.commit()

    headers = login_headers(client, tenant.slug, scoped_admin.email)
    # ordinary resource permissions still work — the group itself is unrestricted
    assert client.get("/api/v1/issued-items", headers=headers).status_code == 200
    # but admin:manage is unconditionally masked out while customer-scoped
    assert client.get("/api/v1/admin/payment-networks", headers=headers).status_code == 403
