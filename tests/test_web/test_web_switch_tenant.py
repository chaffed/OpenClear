# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import webauthn

from openclear.config import get_settings
from openclear.services import security_group_service, user_service
from tests.conftest import TenantFactory
from tests.test_auth.webauthn_helpers import FakeAuthenticator


def _login(client, tenant_slug, email, password=TenantFactory.PASSWORD):
    client.get("/ui/login")
    csrf = client.cookies.get("csrf_token")
    client.post(
        "/ui/login", data={"tenant_slug": tenant_slug, "email": email, "password": password, "csrf_token": csrf, "next": "/ui/"}
    )
    return csrf


def test_switch_tenant_page_lists_other_memberships(client, db_session, tenant_factory):
    tenant_a, _account_a, users_a = tenant_factory.make(name="Switch Corp A", slug="switch-list-a")
    tenant_b, _account_b, _users_b = tenant_factory.make(name="Switch Corp B", slug="switch-list-b")
    group_b = security_group_service.get_security_group_by_name(db_session, tenant_b.id, "Viewer")
    user_service.confirm_cross_tenant_membership(db_session, tenant_b.id, email=users_a["admin"].email, security_group_id=group_b.id)
    db_session.commit()

    _login(client, tenant_a.slug, users_a["admin"].email)
    resp = client.get("/ui/switch-tenant")
    assert resp.status_code == 200
    assert f'>{tenant_b.name} (bank-wide)</button>' in resp.text
    # the current tenant's own name legitimately appears elsewhere on the page (nav
    # brand, title) now that branding shows it — just not as a switch-target button
    assert f'>{tenant_a.name} (bank-wide)</button>' not in resp.text


def test_switch_tenant_without_membership_shows_empty_state(client, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="switch-none")
    _login(client, tenant.slug, users["admin"].email)

    resp = client.get("/ui/switch-tenant")
    assert resp.status_code == 200
    assert "don&#39;t belong" in resp.text or "don't belong" in resp.text


def test_switch_tenant_mints_tokens_without_reauth(client, db_session, tenant_factory):
    tenant_a, _account_a, users_a = tenant_factory.make(slug="switch-mint-a")
    tenant_b, _account_b, _users_b = tenant_factory.make(slug="switch-mint-b")
    group_b = security_group_service.get_security_group_by_name(db_session, tenant_b.id, "Admin")
    membership_b = user_service.confirm_cross_tenant_membership(
        db_session, tenant_b.id, email=users_a["admin"].email, security_group_id=group_b.id
    )
    db_session.commit()

    csrf = _login(client, tenant_a.slug, users_a["admin"].email)
    resp = client.post(
        "/ui/switch-tenant", data={"csrf_token": csrf, "membership_id": str(membership_b.id)}, follow_redirects=False
    )
    assert resp.status_code == 303

    # now operating in tenant_b's context — e.g. tenant_b's security-groups page (Admin
    # group there) is reachable without ever entering a password/MFA for tenant_b
    resp = client.get("/ui/security-groups")
    assert resp.status_code == 200


def test_switch_tenant_rejects_non_member_tenant(client, db_session, tenant_factory):
    import uuid

    tenant_a, _account_a, users_a = tenant_factory.make(slug="switch-reject-a")
    tenant_factory.make(slug="switch-reject-b")

    csrf = _login(client, tenant_a.slug, users_a["admin"].email)
    resp = client.post(
        "/ui/switch-tenant", data={"csrf_token": csrf, "membership_id": str(uuid.uuid4())}, follow_redirects=False
    )
    assert resp.status_code == 303
    assert resp.headers["location"].startswith("/ui/switch-tenant?error")


def test_switch_tenant_to_require_webauthn_target_with_no_key_redirects_to_setup(client, db_session, tenant_factory):
    tenant_a, _account_a, users_a = tenant_factory.make(slug="switch-webauthn-setup-a")
    tenant_b, _account_b, _users_b = tenant_factory.make(slug="switch-webauthn-setup-b")
    group_b = security_group_service.get_security_group_by_name(db_session, tenant_b.id, "Admin")
    membership_b = user_service.confirm_cross_tenant_membership(
        db_session, tenant_b.id, email=users_a["admin"].email, security_group_id=group_b.id, require_webauthn=True
    )
    db_session.commit()

    csrf = _login(client, tenant_a.slug, users_a["admin"].email)
    resp = client.post(
        "/ui/switch-tenant", data={"csrf_token": csrf, "membership_id": str(membership_b.id)}, follow_redirects=False
    )
    assert resp.status_code == 303
    assert resp.headers["location"].startswith("/ui/login/webauthn/setup")
    assert "mfa_token" in resp.cookies
    assert "access_token" not in resp.cookies


def test_switch_tenant_to_require_webauthn_target_with_existing_key_redirects_to_challenge(client, db_session, tenant_factory):
    tenant_a, _account_a, users_a = tenant_factory.make(slug="switch-webauthn-challenge-a")
    tenant_b, _account_b, _users_b = tenant_factory.make(slug="switch-webauthn-challenge-b")
    group_b = security_group_service.get_security_group_by_name(db_session, tenant_b.id, "Admin")
    membership_b = user_service.confirm_cross_tenant_membership(
        db_session, tenant_b.id, email=users_a["admin"].email, security_group_id=group_b.id, require_webauthn=True
    )
    db_session.commit()

    # register a key in tenant_b (credentials are tenant-scoped) via a normal login there first
    csrf = _login(client, tenant_a.slug, users_a["admin"].email)
    switch_resp = client.post(
        "/ui/switch-tenant", data={"csrf_token": csrf, "membership_id": str(membership_b.id)}, follow_redirects=False
    )
    setup_csrf = client.cookies.get("csrf_token")
    fake = FakeAuthenticator(get_settings().webauthn_rp_id, get_settings().webauthn_origin)
    options_resp = client.post("/ui/login/webauthn/setup/options", headers={"X-CSRF-Token": setup_csrf})
    options = webauthn.helpers.parse_registration_options_json(options_resp.text)
    credential = fake.create_registration_credential(options.challenge)
    client.post(
        "/ui/login/webauthn/setup/verify",
        headers={"X-CSRF-Token": setup_csrf},
        params={"next": "/ui/"},
        json={"credential": credential},
    )

    # switch back to tenant_a, then switch into tenant_b again — now a key already exists there
    memberships_a = user_service.list_memberships_for_user(db_session, users_a["admin"].id)
    membership_a = next(m for m in memberships_a if m.tenant.id == tenant_a.id)
    csrf2 = client.cookies.get("csrf_token")
    client.post("/ui/switch-tenant", data={"csrf_token": csrf2, "membership_id": str(membership_a.membership.id)}, follow_redirects=False)

    csrf3 = client.cookies.get("csrf_token")
    resp = client.post(
        "/ui/switch-tenant", data={"csrf_token": csrf3, "membership_id": str(membership_b.id)}, follow_redirects=False
    )
    assert resp.status_code == 303
    assert resp.headers["location"].startswith("/ui/login/webauthn?")
    assert not resp.headers["location"].startswith("/ui/login/webauthn/setup")


def test_switch_tenant_between_ordinary_memberships_is_unaffected(client, db_session, tenant_factory):
    tenant_a, _account_a, users_a = tenant_factory.make(slug="switch-ordinary-a")
    tenant_b, _account_b, _users_b = tenant_factory.make(slug="switch-ordinary-b")
    group_b = security_group_service.get_security_group_by_name(db_session, tenant_b.id, "Admin")
    membership_b = user_service.confirm_cross_tenant_membership(
        db_session, tenant_b.id, email=users_a["admin"].email, security_group_id=group_b.id
    )
    db_session.commit()

    csrf = _login(client, tenant_a.slug, users_a["admin"].email)
    resp = client.post(
        "/ui/switch-tenant", data={"csrf_token": csrf, "membership_id": str(membership_b.id)}, follow_redirects=False
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/ui/"
    assert "access_token" in resp.cookies
