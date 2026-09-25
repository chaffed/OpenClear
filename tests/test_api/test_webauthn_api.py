# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import webauthn

from openclear.config import get_settings
from tests.conftest import login_headers
from tests.test_auth.webauthn_helpers import FakeAuthenticator


def _register_credential(client, headers, fake: FakeAuthenticator, nickname: str = "Test Key") -> dict:
    options_resp = client.post("/api/v1/auth/webauthn/register/options", headers=headers)
    assert options_resp.status_code == 200, options_resp.text
    options = webauthn.helpers.parse_registration_options_json(options_resp.text)
    credential = fake.create_registration_credential(options.challenge)

    verify_resp = client.post(
        "/api/v1/auth/webauthn/register/verify",
        headers=headers,
        json={"credential": credential, "nickname": nickname},
    )
    assert verify_resp.status_code == 201, verify_resp.text
    return verify_resp.json()


def test_login_without_registered_key_is_unchanged(client, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="webauthn-no-mfa")

    resp = client.post(
        "/api/v1/auth/login",
        json={"tenant_slug": tenant.slug, "email": users["admin"].email, "password": tenant_factory.PASSWORD},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["mfa_required"] is False
    assert body["access_token"]
    assert body["mfa_token"] is None


def test_full_register_and_mfa_login_flow(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="webauthn-full-flow")
    user = users["admin"]
    headers = login_headers(client, tenant.slug, user.email)
    fake = FakeAuthenticator(get_settings().webauthn_rp_id, get_settings().webauthn_origin)

    registered = _register_credential(client, headers, fake)
    assert registered["nickname"] == "Test Key"

    # Login now requires the second factor instead of returning real tokens directly.
    login_resp = client.post(
        "/api/v1/auth/login",
        json={"tenant_slug": tenant.slug, "email": user.email, "password": tenant_factory.PASSWORD},
    )
    assert login_resp.status_code == 200
    login_body = login_resp.json()
    assert login_body["mfa_required"] is True
    assert login_body["access_token"] is None
    mfa_token = login_body["mfa_token"]
    mfa_headers = {"Authorization": f"Bearer {mfa_token}"}

    # The mfa_token alone must not work as a normal access token.
    denied = client.get("/api/v1/issued-items", headers=mfa_headers)
    assert denied.status_code == 401

    options_resp = client.post("/api/v1/auth/webauthn/login/options", headers=mfa_headers)
    assert options_resp.status_code == 200, options_resp.text
    auth_options = webauthn.helpers.parse_authentication_options_json(options_resp.text)
    assertion = fake.create_authentication_credential(auth_options.challenge)

    verify_resp = client.post(
        "/api/v1/auth/webauthn/login/verify", headers=mfa_headers, json={"credential": assertion}
    )
    assert verify_resp.status_code == 200, verify_resp.text
    tokens = verify_resp.json()
    assert tokens["access_token"]

    # The real access token now works normally.
    ok = client.get("/api/v1/issued-items", headers={"Authorization": f"Bearer {tokens['access_token']}"})
    assert ok.status_code == 200

    from openclear.repositories.user_repo import UserRepository

    db_session.expire_all()
    assert UserRepository(db_session).get(user.id).last_login_at is not None


def test_wrong_credential_signature_rejected_at_login_verify(client, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="webauthn-wrong-sig")
    user = users["admin"]
    headers = login_headers(client, tenant.slug, user.email)
    settings = get_settings()
    fake = FakeAuthenticator(settings.webauthn_rp_id, settings.webauthn_origin)
    _register_credential(client, headers, fake)

    login_resp = client.post(
        "/api/v1/auth/login",
        json={"tenant_slug": tenant.slug, "email": user.email, "password": tenant_factory.PASSWORD},
    )
    mfa_headers = {"Authorization": f"Bearer {login_resp.json()['mfa_token']}"}
    client.post("/api/v1/auth/webauthn/login/options", headers=mfa_headers)

    # An assertion from a DIFFERENT (unregistered) authenticator must be rejected.
    impostor = FakeAuthenticator(settings.webauthn_rp_id, settings.webauthn_origin)
    fake_challenge = webauthn.helpers.generate_challenge()
    forged_assertion = impostor.create_authentication_credential(fake_challenge)

    resp = client.post("/api/v1/auth/webauthn/login/verify", headers=mfa_headers, json={"credential": forged_assertion})
    assert resp.status_code == 400


def test_list_and_delete_credentials_via_api(client, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="webauthn-crud")
    user = users["admin"]
    headers = login_headers(client, tenant.slug, user.email)
    fake = FakeAuthenticator(get_settings().webauthn_rp_id, get_settings().webauthn_origin)

    registered = _register_credential(client, headers, fake)

    listed = client.get("/api/v1/auth/webauthn/credentials", headers=headers)
    assert listed.status_code == 200
    assert [c["id"] for c in listed.json()] == [registered["id"]]

    deleted = client.delete(f"/api/v1/auth/webauthn/credentials/{registered['id']}", headers=headers)
    assert deleted.status_code == 204

    listed_after = client.get("/api/v1/auth/webauthn/credentials", headers=headers)
    assert listed_after.json() == []


def test_credentials_are_tenant_isolated(client, tenant_factory):
    tenant_a, _account_a, users_a = tenant_factory.make(slug="webauthn-iso-a")
    tenant_b, _account_b, users_b = tenant_factory.make(slug="webauthn-iso-b")
    headers_a = login_headers(client, tenant_a.slug, users_a["admin"].email)
    headers_b = login_headers(client, "webauthn-iso-b", users_b["admin"].email)

    fake = FakeAuthenticator(get_settings().webauthn_rp_id, get_settings().webauthn_origin)
    registered = _register_credential(client, headers_a, fake)

    # Tenant B's admin must not see or be able to delete tenant A's credential.
    listed_b = client.get("/api/v1/auth/webauthn/credentials", headers=headers_b)
    assert listed_b.json() == []

    deleted_b = client.delete(f"/api/v1/auth/webauthn/credentials/{registered['id']}", headers=headers_b)
    assert deleted_b.status_code == 404
