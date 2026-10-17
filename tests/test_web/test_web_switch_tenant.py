# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import webauthn

from openclear.auth.security import create_session_tokens, decode_token
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


def test_switch_tenant_to_require_webauthn_target_with_no_key_is_refused(client, db_session, tenant_factory):
    # Registering a key there from a session that came from elsewhere would let a stolen
    # session cookie add its own key; the person signs in there directly instead.
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
    assert resp.headers["location"].startswith("/ui/switch-tenant?error=")
    assert "security%20key" in resp.headers["location"]
    assert "mfa_token" not in resp.cookies
    assert "access_token" not in resp.cookies


def _register_key_by_signing_in(client, tenant_slug, email):
    """Signs in to `tenant_slug` directly, where the membership requires a key and none
    exists yet, and registers one through the sign-in's own setup step."""
    _login(client, tenant_slug, email)
    setup_csrf = client.cookies.get("csrf_token")
    fake = FakeAuthenticator(get_settings().webauthn_rp_id, get_settings().webauthn_origin)
    options_resp = client.post("/ui/login/webauthn/setup/options", headers={"X-CSRF-Token": setup_csrf})
    options = webauthn.helpers.parse_registration_options_json(options_resp.text)
    credential = fake.create_registration_credential(options.challenge)
    resp = client.post(
        "/ui/login/webauthn/setup/verify",
        headers={"X-CSRF-Token": setup_csrf},
        params={"next": "/ui/"},
        json={"credential": credential},
    )
    assert resp.status_code == 200, resp.text


def test_switch_tenant_to_require_webauthn_target_with_existing_key_redirects_to_challenge(client, db_session, tenant_factory):
    tenant_a, _account_a, users_a = tenant_factory.make(slug="switch-webauthn-challenge-a")
    tenant_b, _account_b, _users_b = tenant_factory.make(slug="switch-webauthn-challenge-b")
    group_b = security_group_service.get_security_group_by_name(db_session, tenant_b.id, "Admin")
    membership_b = user_service.confirm_cross_tenant_membership(
        db_session, tenant_b.id, email=users_a["admin"].email, security_group_id=group_b.id, require_webauthn=True
    )
    db_session.commit()
    _register_key_by_signing_in(client, tenant_b.slug, users_a["admin"].email)

    csrf = _login(client, tenant_a.slug, users_a["admin"].email)
    resp = client.post(
        "/ui/switch-tenant", data={"csrf_token": csrf, "membership_id": str(membership_b.id)}, follow_redirects=False
    )
    assert resp.status_code == 303
    assert resp.headers["location"].startswith("/ui/login/webauthn?")
    assert decode_token(resp.cookies["mfa_token"])["am"] == "password"  # carried into the session the key completes


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


# --- Switching uses this session's sign-in, so only where that sign-in is accepted ---


def _two_banks(db_session, tenant_factory, slug, **membership_kwargs):
    tenant_a, _account_a, users_a = tenant_factory.make(slug=f"{slug}-a")
    tenant_b, _account_b, _users_b = tenant_factory.make(slug=f"{slug}-b")
    group_b = security_group_service.get_security_group_by_name(db_session, tenant_b.id, "Admin")
    membership_b = user_service.confirm_cross_tenant_membership(
        db_session, tenant_b.id, email=users_a["admin"].email, security_group_id=group_b.id, **membership_kwargs
    )
    db_session.commit()
    return tenant_a, users_a["admin"], tenant_b, membership_b


def _use_session(client, db_session, tenant, user, **claims):
    """Puts a session for `user` at `tenant` (bank-wide Admin) in the client's cookies, as
    if signed in some other way than the password form, and returns a CSRF token."""
    group = security_group_service.get_security_group_by_name(db_session, tenant.id, "Admin")
    tokens = create_session_tokens(
        user_id=user.id, tenant_id=tenant.id, security_group_id=group.id, customer_id=None,
        token_version=user.token_version, **claims,
    )
    client.get("/ui/login")
    _set_session_cookies(client, tokens.access_token, tokens.refresh_token)
    return client.cookies.get("csrf_token")


def _set_session_cookies(client, access_token, refresh_token):
    for name in ("access_token", "refresh_token"):
        for cookie in [c for c in client.cookies.jar if c.name == name]:
            client.cookies.jar.clear(cookie.domain, cookie.path, cookie.name)
    client.cookies.set("access_token", access_token)
    client.cookies.set("refresh_token", refresh_token)


def _switch(client, csrf, membership_id):
    return client.post("/ui/switch-tenant", data={"csrf_token": csrf, "membership_id": str(membership_id)}, follow_redirects=False)


def _refused(resp):
    return resp.status_code == 303 and resp.headers["location"].startswith("/ui/switch-tenant?error=") and "access_token" not in resp.cookies


def test_password_session_carries_its_sign_in_method_through_a_switch(client, db_session, tenant_factory):
    tenant_a, user, _tenant_b, membership_b = _two_banks(db_session, tenant_factory, "switch-am")
    csrf = _login(client, tenant_a.slug, user.email)
    assert decode_token(client.cookies.get("access_token"))["am"] == "password"

    resp = _switch(client, csrf, membership_b.id)
    assert resp.headers["location"] == "/ui/"
    assert decode_token(resp.cookies["access_token"])["am"] == "password"


def test_password_session_cant_switch_to_a_bank_that_turned_passwords_off(client, db_session, tenant_factory):
    tenant_a, user, tenant_b, membership_b = _two_banks(db_session, tenant_factory, "switch-sso-only")
    tenant_b.password_login_enabled = False
    db_session.commit()

    csrf = _login(client, tenant_a.slug, user.email)
    assert _refused(_switch(client, csrf, membership_b.id))


def test_password_session_can_switch_to_a_password_off_bank_where_the_membership_requires_a_key(
    client, db_session, tenant_factory
):
    # require_webauthn is the compensating control that lets this membership use a
    # password where its bank requires SSO (login_service.scope_allows_password_login).
    tenant_a, user, tenant_b, membership_b = _two_banks(db_session, tenant_factory, "switch-sso-key", require_webauthn=True)
    _register_key_by_signing_in(client, tenant_b.slug, user.email)
    tenant_b.password_login_enabled = False
    db_session.commit()

    csrf = _login(client, tenant_a.slug, user.email)
    assert _switch(client, csrf, membership_b.id).headers["location"].startswith("/ui/login/webauthn?")


def test_sso_session_cant_switch_to_another_bank(client, db_session, tenant_factory):
    tenant_a, user, _tenant_b, membership_b = _two_banks(db_session, tenant_factory, "switch-sso")
    csrf = _use_session(client, db_session, tenant_a, user, signed_in_with="sso")
    assert _refused(_switch(client, csrf, membership_b.id))


def test_sso_session_can_switch_within_its_own_bank(client, db_session, tenant_factory):
    from openclear.services import customer_service

    tenant_a, _account_a, users_a = tenant_factory.make(slug="switch-sso-same")
    customer = customer_service.create_customer(
        db_session, tenant_a.id, customer_service.CustomerInput(customer_number="C-1", name="Same Bank Co")
    )
    viewer = security_group_service.get_security_group_by_name(db_session, tenant_a.id, "Viewer")
    membership = user_service.confirm_cross_tenant_membership(
        db_session, tenant_a.id, email=users_a["admin"].email, security_group_id=viewer.id, customer_id=customer.id
    )
    db_session.commit()

    csrf = _use_session(client, db_session, tenant_a, users_a["admin"], signed_in_with="sso")
    resp = _switch(client, csrf, membership.id)
    assert resp.headers["location"] == "/ui/"
    assert decode_token(resp.cookies["access_token"])["am"] == "sso"


def test_session_from_before_the_claim_cant_switch(client, db_session, tenant_factory):
    tenant_a, user, _tenant_b, membership_b = _two_banks(db_session, tenant_factory, "switch-legacy")
    csrf = _use_session(client, db_session, tenant_a, user)  # no `am` claim
    assert _refused(_switch(client, csrf, membership_b.id))


def test_embedded_session_cant_switch(client, db_session, tenant_factory):
    tenant_a, user, _tenant_b, membership_b = _two_banks(db_session, tenant_factory, "switch-embed")
    csrf = _use_session(client, db_session, tenant_a, user, signed_in_with="password", embed={"po": "https://bank.example"})
    assert _refused(_switch(client, csrf, membership_b.id))


def test_sign_out_other_devices_keeps_the_session_claims(client, db_session, tenant_factory):
    # The re-issued tokens once dropped `cfd`, `emb` and `am`, so a confined session could
    # switch after pressing "Sign out other devices".
    tenant_a, user, _tenant_b, membership_b = _two_banks(db_session, tenant_factory, "switch-reissue")
    csrf = _use_session(client, db_session, tenant_a, user, signed_in_with="sso", confined=True)
    resp = client.post("/ui/security/sign-out-others", data={"csrf_token": csrf}, follow_redirects=False)
    claims = decode_token(resp.cookies["access_token"])
    assert claims["cfd"] == 1 and claims["am"] == "sso"
    _set_session_cookies(client, resp.cookies["access_token"], resp.cookies["refresh_token"])
    assert _refused(_switch(client, client.cookies.get("csrf_token"), membership_b.id))
