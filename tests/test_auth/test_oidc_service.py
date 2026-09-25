# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import pytest

from openclear.auth.crypto import encrypt_secret
from openclear.auth.oidc_service import OidcError, build_authorization_url, exchange_code_for_claims, reset_oidc_cache
from openclear.domain.sso_connection import SsoConnection, SsoProvider
from tests.test_auth.oidc_helpers import FakeOidcProvider, patch_provider


def _make_connection(**overrides) -> SsoConnection:
    defaults = dict(
        provider=SsoProvider.OKTA,
        display_name="Test Okta",
        issuer="https://idp.example.com",
        client_id="test-client-id",
        client_secret_encrypted=encrypt_secret("fake-secret"),
        groups_claim_name="groups",
        auto_provision=False,
        is_active=True,
    )
    defaults.update(overrides)
    return SsoConnection(**defaults)


def test_build_authorization_url_includes_expected_params(monkeypatch):
    reset_oidc_cache()
    provider = FakeOidcProvider()
    monkeypatch.setattr(
        "openclear.auth.oidc_service._get_discovery_document", lambda issuer: provider.discovery_document()
    )
    connection = _make_connection()

    url = build_authorization_url(connection, redirect_uri="https://app.example.com/callback", state="s1", nonce="n1")

    assert url.startswith("https://idp.example.com/authorize")
    assert "client_id=test-client-id" in url
    assert "state=s1" in url
    assert "nonce=n1" in url


def test_exchange_code_for_claims_success_with_groups(monkeypatch):
    reset_oidc_cache()
    provider = FakeOidcProvider()
    connection = _make_connection()
    id_token = provider.sign_id_token(sub="user-1", email="person@example.com", groups=["openclear-approvers"], nonce="the-nonce")
    patch_provider(monkeypatch, provider, id_token)

    claims = exchange_code_for_claims(connection, code="fake-code", redirect_uri="https://app.example.com/callback", expected_nonce="the-nonce")

    assert claims.email == "person@example.com"
    assert claims.subject == "user-1"
    assert claims.groups == ["openclear-approvers"]


def test_exchange_code_for_claims_falls_back_to_preferred_username(monkeypatch):
    reset_oidc_cache()
    provider = FakeOidcProvider()
    connection = _make_connection()
    id_token = provider.sign_id_token(sub="user-2", preferred_username="person@example.com", nonce="n")
    patch_provider(monkeypatch, provider, id_token)

    claims = exchange_code_for_claims(connection, code="c", redirect_uri="https://app.example.com/callback", expected_nonce="n")

    assert claims.email == "person@example.com"


def test_exchange_code_for_claims_rejects_wrong_nonce(monkeypatch):
    reset_oidc_cache()
    provider = FakeOidcProvider()
    connection = _make_connection()
    id_token = provider.sign_id_token(sub="user-1", email="a@b.com", nonce="actual-nonce")
    patch_provider(monkeypatch, provider, id_token)

    with pytest.raises(OidcError, match="nonce"):
        exchange_code_for_claims(connection, code="c", redirect_uri="https://app.example.com/callback", expected_nonce="different-nonce")


def test_exchange_code_for_claims_rejects_wrong_audience(monkeypatch):
    reset_oidc_cache()
    provider = FakeOidcProvider()
    connection = _make_connection()
    id_token = provider.sign_id_token(sub="user-1", email="a@b.com", aud="some-other-client", nonce="n")
    patch_provider(monkeypatch, provider, id_token)

    with pytest.raises(OidcError, match="audience"):
        exchange_code_for_claims(connection, code="c", redirect_uri="https://app.example.com/callback", expected_nonce="n")


def test_exchange_code_for_claims_rejects_expired_token(monkeypatch):
    reset_oidc_cache()
    provider = FakeOidcProvider()
    connection = _make_connection()
    id_token = provider.sign_id_token(sub="user-1", email="a@b.com", nonce="n", exp_delta_seconds=-60)
    patch_provider(monkeypatch, provider, id_token)

    with pytest.raises(OidcError, match="expired"):
        exchange_code_for_claims(connection, code="c", redirect_uri="https://app.example.com/callback", expected_nonce="n")


def test_exchange_code_for_claims_rejects_bad_signature(monkeypatch):
    """Signed with a key that is NOT in the provider's own published JWKS — simulates a
    forged/tampered token and confirms the real signature-verification path rejects it."""
    from joserfc.jwk import RSAKey

    reset_oidc_cache()
    provider = FakeOidcProvider()
    connection = _make_connection()
    attacker_key = RSAKey.generate_key(2048, parameters={"kid": "test-kid"}, private=True)
    id_token = provider.sign_id_token(sub="user-1", email="a@b.com", nonce="n", key=attacker_key)
    patch_provider(monkeypatch, provider, id_token)

    with pytest.raises(OidcError, match="signature"):
        exchange_code_for_claims(connection, code="c", redirect_uri="https://app.example.com/callback", expected_nonce="n")


def test_exchange_code_for_claims_rejects_missing_email_and_username(monkeypatch):
    reset_oidc_cache()
    provider = FakeOidcProvider()
    connection = _make_connection()
    id_token = provider.sign_id_token(sub="user-1", nonce="n")
    patch_provider(monkeypatch, provider, id_token)

    with pytest.raises(OidcError, match="email"):
        exchange_code_for_claims(connection, code="c", redirect_uri="https://app.example.com/callback", expected_nonce="n")


def test_discovery_document_is_cached_across_calls(monkeypatch):
    """Exercises the REAL _get_discovery_document cache dict — only the underlying HTTP
    GET is stubbed, so a second call within the cache TTL must not hit it again."""
    import openclear.auth.oidc_service as oidc_service

    reset_oidc_cache()
    provider = FakeOidcProvider()
    call_count = {"n": 0}

    class _FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            call_count["n"] += 1
            return provider.discovery_document()

    class _FakeHttpClient:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def get(self, url, **kwargs):
            return _FakeResponse()

    monkeypatch.setattr(oidc_service, "_http_client", lambda: _FakeHttpClient())
    connection = _make_connection()

    build_authorization_url(connection, redirect_uri="https://app.example.com/callback", state="s", nonce="n")
    build_authorization_url(connection, redirect_uri="https://app.example.com/callback", state="s2", nonce="n2")

    assert call_count["n"] == 1
