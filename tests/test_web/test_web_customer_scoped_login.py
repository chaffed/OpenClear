# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Web/cookie-auth channel coverage for customer scoping: default membership selection at
login when a user holds several memberships in one tenant, switching between them via
/ui/switch-tenant, and confirming CUSTOMER_SCOPE_MASKED_PERMISSIONS holds even for a
customer-scoped membership using the full "Admin" security group — the web-UI analogue of
test_cross_customer_isolation.py's API-channel masking test."""

import datetime as dt

from openclear.repositories.tenant_membership_repo import TenantMembershipRepository
from openclear.services import customer_service, security_group_service, user_service
from tests.conftest import TenantFactory


def _login(client, tenant_slug, email, password=TenantFactory.PASSWORD):
    client.get("/ui/login")
    csrf = client.cookies.get("csrf_token")
    client.post(
        "/ui/login", data={"tenant_slug": tenant_slug, "email": email, "password": password, "csrf_token": csrf, "next": "/ui/"}
    )
    return csrf


def test_login_defaults_to_bank_wide_membership_when_multiple_exist(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="web-cust-login-default")
    customer = customer_service.create_customer(
        db_session, tenant.id, customer_service.CustomerInput(customer_number="C-1", name="Scoped Co")
    )
    preparer_group = security_group_service.get_security_group_by_name(db_session, tenant.id, "Preparer")
    # give the tenant-wide admin an ADDITIONAL, customer-scoped membership too
    user_service.confirm_cross_tenant_membership(
        db_session, tenant.id, email=users["admin"].email, security_group_id=preparer_group.id, customer_id=customer.id
    )
    db_session.commit()

    _login(client, tenant.slug, users["admin"].email)

    # bank-wide membership wins by default -> full admin reach, no scope badge
    resp = client.get("/ui/")
    assert resp.status_code == 200
    assert "customer-scope-badge" not in resp.text
    assert client.get("/ui/users").status_code == 200


def test_login_defaults_to_earliest_customer_membership_when_no_bank_wide_exists(client, db_session, tenant_factory):
    tenant, _account, _users = tenant_factory.make(slug="web-cust-login-no-bankwide")
    customer_a = customer_service.create_customer(
        db_session, tenant.id, customer_service.CustomerInput(customer_number="C-A", name="Alpha Co")
    )
    customer_b = customer_service.create_customer(
        db_session, tenant.id, customer_service.CustomerInput(customer_number="C-B", name="Beta Co")
    )
    preparer_group = security_group_service.get_security_group_by_name(db_session, tenant.id, "Preparer")
    user = user_service.create_user_with_membership(
        db_session, tenant.id, email="multi@example.com", password=TenantFactory.PASSWORD,
        security_group_id=preparer_group.id, customer_id=customer_a.id,
    )
    user_service.confirm_cross_tenant_membership(
        db_session, tenant.id, email=user.email, security_group_id=preparer_group.id, customer_id=customer_b.id
    )
    db_session.commit()

    # Force a real time gap between the two memberships' created_at — SQLite's
    # CURRENT_TIMESTAMP only has one-second resolution, so two memberships created back
    # to back in a test would otherwise tie, making "earliest wins" nondeterministic to
    # test (see auth/login_service.py's tiebreak comment for the same issue in prod).
    memberships = TenantMembershipRepository(db_session, tenant.id).list(user_id=user.id)
    membership_a = next(m for m in memberships if m.customer_id == customer_a.id)
    membership_b = next(m for m in memberships if m.customer_id == customer_b.id)
    membership_a.created_at = dt.datetime(2020, 1, 1, tzinfo=dt.timezone.utc)
    membership_b.created_at = dt.datetime(2020, 1, 2, tzinfo=dt.timezone.utc)
    db_session.commit()

    _login(client, tenant.slug, user.email)

    resp = client.get("/ui/")
    assert resp.status_code == 200
    assert "Alpha Co" in resp.text


def test_switch_tenant_lists_customer_scope_per_row(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="web-cust-switch-list")
    customer = customer_service.create_customer(
        db_session, tenant.id, customer_service.CustomerInput(customer_number="C-1", name="Widgets Inc")
    )
    preparer_group = security_group_service.get_security_group_by_name(db_session, tenant.id, "Preparer")
    user_service.confirm_cross_tenant_membership(
        db_session, tenant.id, email=users["admin"].email, security_group_id=preparer_group.id, customer_id=customer.id
    )
    db_session.commit()

    _login(client, tenant.slug, users["admin"].email)
    resp = client.get("/ui/switch-tenant")
    assert resp.status_code == 200
    assert "Widgets Inc" in resp.text


def test_switch_into_customer_scope_shows_badge_and_masks_admin_routes(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="web-cust-switch-into")
    customer = customer_service.create_customer(
        db_session, tenant.id, customer_service.CustomerInput(customer_number="C-1", name="Gadgets LLC")
    )
    preparer_group = security_group_service.get_security_group_by_name(db_session, tenant.id, "Preparer")
    membership = user_service.confirm_cross_tenant_membership(
        db_session, tenant.id, email=users["admin"].email, security_group_id=preparer_group.id, customer_id=customer.id
    )
    db_session.commit()

    csrf = _login(client, tenant.slug, users["admin"].email)
    resp = client.post(
        "/ui/switch-tenant", data={"csrf_token": csrf, "membership_id": str(membership.id)}, follow_redirects=False
    )
    assert resp.status_code == 303

    home = client.get("/ui/")
    assert "customer-scope-badge" in home.text
    assert "Gadgets LLC" in home.text
    assert client.get("/ui/accounts").status_code == 200


def test_customer_scoped_admin_group_is_masked_from_tenant_admin_web_routes(client, db_session, tenant_factory):
    """Mirrors test_cross_customer_isolation.py's API-channel version, but through the
    cookie-auth web UI: a membership using the full "Admin" security group, once
    customer-scoped, still can't reach /ui/users, /ui/security-groups, /ui/settings, or
    /ui/audit-log — auth/permissions.py::CUSTOMER_SCOPE_MASKED_PERMISSIONS."""
    tenant, _account, _users = tenant_factory.make(slug="web-cust-masked-admin")
    customer = customer_service.create_customer(
        db_session, tenant.id, customer_service.CustomerInput(customer_number="C-1", name="Masked Co")
    )
    admin_group = security_group_service.get_security_group_by_name(db_session, tenant.id, "Admin")
    scoped_admin = user_service.create_user_with_membership(
        db_session, tenant.id, email="scoped-admin@example.com", password=TenantFactory.PASSWORD,
        security_group_id=admin_group.id, customer_id=customer.id,
    )
    db_session.commit()

    _login(client, tenant.slug, scoped_admin.email)

    # ordinary resource access still works — the underlying group is unrestricted
    assert client.get("/ui/accounts").status_code == 200
    # but every tenant-admin-only surface is masked out regardless
    assert client.get("/ui/users", follow_redirects=False).status_code == 403
    assert client.get("/ui/security-groups", follow_redirects=False).status_code == 403
    # Settings is the customer's own control panel (its reminders and banner), never the bank's.
    customer_panel = client.get("/ui/settings", follow_redirects=False)
    assert customer_panel.status_code == 200
    assert 'data-key="reminders"' in customer_panel.text
    assert 'data-key="branding"' not in customer_panel.text
    assert client.get("/ui/settings/branding", follow_redirects=False).status_code == 403
    assert client.get("/ui/audit-log", follow_redirects=False).status_code == 403
    assert client.get("/ui/customers", follow_redirects=False).status_code == 403
