# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

from openclear.repositories.tenant_membership_repo import TenantMembershipRepository
from openclear.services import customer_service, security_group_service, user_service
from tests.conftest import TenantFactory, login_headers


def test_list_users_requires_permission(client, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="api-users-forbidden")
    headers = login_headers(client, tenant.slug, users["viewer"].email)

    resp = client.get("/api/v1/users", headers=headers)
    assert resp.status_code == 403


def test_list_users_matches_web_list(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="api-users-list")
    headers = login_headers(client, tenant.slug, users["admin"].email)

    resp = client.get("/api/v1/users", headers=headers)
    assert resp.status_code == 200
    body = resp.json()

    emails = {row["email"] for row in body}
    assert emails == {u.email for u in users.values()}

    admin_row = next(row for row in body if row["email"] == users["admin"].email)
    assert admin_row["security_group_name"] == "Admin"
    assert admin_row["customer_name"] is None
    assert admin_row["is_active"] is True
    assert "membership_id" in admin_row
    assert "membership_created_at" in admin_row


def test_list_users_shows_one_row_per_membership_for_multi_scope_user(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="api-users-multi-scope")
    customer = customer_service.create_customer(
        db_session, tenant.id, customer_service.CustomerInput(customer_number="C-1", name="Scoped Co")
    )
    preparer_group = security_group_service.get_security_group_by_name(db_session, tenant.id, "Preparer")
    user_service.confirm_cross_tenant_membership(
        db_session, tenant.id, email=users["admin"].email, security_group_id=preparer_group.id, customer_id=customer.id
    )
    db_session.commit()

    headers = login_headers(client, tenant.slug, users["admin"].email)
    resp = client.get("/api/v1/users", headers=headers)
    assert resp.status_code == 200
    admin_rows = [row for row in resp.json() if row["email"] == users["admin"].email]
    assert len(admin_rows) == 2
    assert {row["customer_name"] for row in admin_rows} == {None, "Scoped Co"}


def test_list_users_reflects_deactivated_status(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="api-users-deactivated")
    membership = TenantMembershipRepository(db_session, tenant.id).list(user_id=users["viewer"].id)[0]
    user_service.deactivate_membership(db_session, tenant.id, membership.id)
    db_session.commit()

    headers = login_headers(client, tenant.slug, users["admin"].email)
    resp = client.get("/api/v1/users", headers=headers)
    viewer_row = next(row for row in resp.json() if row["email"] == users["viewer"].email)
    assert viewer_row["is_active"] is False


def test_list_users_includes_last_login_at_after_login(client, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="api-users-last-login")
    admin_headers = login_headers(client, tenant.slug, users["admin"].email)

    # the viewer hasn't logged in yet in this test -> last_login_at is still null
    resp_before = client.get("/api/v1/users", headers=admin_headers)
    viewer_row_before = next(row for row in resp_before.json() if row["email"] == users["viewer"].email)
    assert viewer_row_before["last_login_at"] is None

    login_headers(client, tenant.slug, users["viewer"].email)

    resp_after = client.get("/api/v1/users", headers=admin_headers)
    viewer_row_after = next(row for row in resp_after.json() if row["email"] == users["viewer"].email)
    assert viewer_row_after["last_login_at"] is not None
