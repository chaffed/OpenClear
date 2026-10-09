# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""A fake OIDC identity provider for tests: a real RSA keypair signs id_tokens exactly
like a real IdP would, and the matching public JWKS is what auth/oidc_service.py's real
verification code checks them against — same philosophy as
tests/test_auth/webauthn_helpers.py hand-constructing real crypto material instead of
mocking the verification logic itself."""

import time

from joserfc import jwt as joserfc_jwt
from joserfc.jwk import KeySet, RSAKey


class FakeOidcProvider:
    def __init__(self, issuer: str = "https://idp.example.com", client_id: str = "test-client-id"):
        self.issuer = issuer
        self.client_id = client_id
        self._key = RSAKey.generate_key(2048, parameters={"kid": "test-kid"}, private=True)
        self.token_requests: list[dict] = []  # what each token-endpoint call sent

    def discovery_document(self) -> dict:
        return {
            "issuer": self.issuer,
            "authorization_endpoint": f"{self.issuer}/authorize",
            "token_endpoint": f"{self.issuer}/token",
            "jwks_uri": f"{self.issuer}/jwks",
        }

    def jwks(self) -> KeySet:
        public_dict = KeySet([self._key]).as_dict(private=False)
        return KeySet.import_key_set(public_dict)

    def sign_id_token(
        self,
        *,
        sub: str,
        email: str | None = None,
        preferred_username: str | None = None,
        groups: list[str] | None = None,
        nonce: str = "test-nonce",
        aud: str | None = None,
        issuer: str | None = None,
        exp_delta_seconds: int = 300,
        key: RSAKey | None = None,
    ) -> str:
        claims = {
            "iss": issuer or self.issuer,
            "aud": aud or self.client_id,
            "sub": sub,
            "exp": int(time.time()) + exp_delta_seconds,
            "nonce": nonce,
        }
        if email is not None:
            claims["email"] = email
        if preferred_username is not None:
            claims["preferred_username"] = preferred_username
        if groups is not None:
            claims["groups"] = groups
        header = {"alg": "RS256", "kid": "test-kid"}
        return joserfc_jwt.encode(header, claims, key or self._key)


def patch_provider(monkeypatch, provider: FakeOidcProvider, id_token: str) -> None:
    """Wires provider.discovery_document()/jwks() into oidc_service's cache functions,
    and stubs the token-endpoint HTTP exchange to return the given id_token directly —
    no real network call, but the id_token itself is verified for real."""
    import openclear.auth.oidc_service as oidc_service

    monkeypatch.setattr(oidc_service, "_get_discovery_document", lambda issuer: provider.discovery_document())
    monkeypatch.setattr(oidc_service, "_get_jwks", lambda jwks_uri: provider.jwks())

    class _FakeOAuth2Client:
        def __init__(self, *args, **kwargs):
            pass

        def fetch_token(self, url, code=None, **kwargs):
            provider.token_requests.append({"code": code, **kwargs})
            return {"id_token": id_token}

    monkeypatch.setattr(oidc_service, "OAuth2Client", _FakeOAuth2Client)


def sign_logout_token(provider: FakeOidcProvider, *, sub: str | None, jti: str = "logout-1", iat_delta_seconds: int = 0,
                      with_event: bool = True, nonce: str | None = None, aud: str | None = None, key: RSAKey | None = None) -> str:
    """An OpenID Connect Back-Channel Logout token, signed like the provider's ID tokens."""
    claims = {"iss": provider.issuer, "aud": aud or provider.client_id, "iat": int(time.time()) + iat_delta_seconds, "jti": jti}
    if sub is not None:
        claims["sub"] = sub
    if with_event:
        claims["events"] = {"http://schemas.openid.net/event/backchannel-logout": {}}
    if nonce is not None:
        claims["nonce"] = nonce
    return joserfc_jwt.encode({"alg": "RS256", "kid": "test-kid"}, claims, key or provider._key)
