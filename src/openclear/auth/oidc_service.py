# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import secrets
import time
from dataclasses import dataclass, field
from typing import Any

import httpx
from authlib.integrations.httpx_client import OAuth2Client
from joserfc import jwt as joserfc_jwt
from joserfc.jwk import KeySet

from openclear.auth.crypto import decrypt_secret
from openclear.auth.outbound_http import UnsafeUrlError, check_url, public_only_client, public_only_transport
from openclear.config import get_settings
from openclear.domain.sso_connection import SsoConnection

_CACHE_TTL_SECONDS = 3600

# In-process caches, keyed by issuer / jwks_uri — both are fetched over the network from
# the identity provider, so short-TTL caching avoids a round-trip on every single login
# without risking a long-stale key set. reset_oidc_cache() is test-only, mirroring
# ml/predict.py::reset_model_cache's role for that module's own cache.
_discovery_cache: dict[str, tuple[float, dict]] = {}
_jwks_cache: dict[str, tuple[float, KeySet]] = {}


class OidcError(Exception):
    """Raised for any part of the OIDC exchange/verification failing — callers map this
    to a clear login-page error, never a 500. Mirrors WebauthnError's role for the other
    browser-driven auth ceremony in this app: expired/invalid state, a rejected code
    exchange, or a signature/claims mismatch are all legitimate outcomes here, not bugs."""


@dataclass(frozen=True, slots=True)
class OidcClaims:
    email: str
    subject: str
    groups: list[str] = field(default_factory=list)
    # The online banking sign-on profile (SsoConnection.customer_claim): which business
    # customer the person belongs to, by customer number.
    customer_number: str | None = None
    # What the provider said about how the person authenticated: OIDC amr and acr values, or
    # SAML AuthnContextClassRef (services/sso_service.py::mfa_reported).
    authentication: tuple[str, ...] = ()


def _http_client() -> httpx.Client:
    # Only ever reaches public addresses, never follows redirects — see
    # auth/outbound_http.py for why an admin-typed issuer can't be fetched naively.
    return public_only_client(timeout=get_settings().oidc_http_timeout_seconds)


def _discovery(connection: SsoConnection) -> dict[str, Any]:
    """The provider's discovery document, with every endpoint it names checked too: a
    malicious or compromised issuer could otherwise point jwks_uri/token_endpoint at an
    internal address, or authorization_endpoint at a non-https URL. Network failures and
    unsafe URLs both become OidcError, so the login page shows a clean error, not a 500."""
    try:
        check_url(connection.issuer, what="issuer URL")
        document = _get_discovery_document(connection.issuer)
        for key in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
            if not isinstance(document.get(key), str):
                raise OidcError(f"The provider's discovery document has no {key}")
            check_url(document[key], what=key)
    except UnsafeUrlError as exc:
        raise OidcError(str(exc)) from exc
    except (httpx.HTTPError, ValueError) as exc:
        raise OidcError(f"Couldn't read the provider's discovery document: {exc}") from exc
    return document


def _get_discovery_document(issuer: str) -> dict[str, Any]:
    now = time.monotonic()
    cached = _discovery_cache.get(issuer)
    if cached is not None and cached[0] > now:
        return cached[1]
    with _http_client() as client:
        response = client.get(f"{issuer.rstrip('/')}/.well-known/openid-configuration")
        response.raise_for_status()
        document = response.json()
    _discovery_cache[issuer] = (now + _CACHE_TTL_SECONDS, document)
    return document


def _get_jwks(jwks_uri: str) -> KeySet:
    now = time.monotonic()
    cached = _jwks_cache.get(jwks_uri)
    if cached is not None and cached[0] > now:
        return cached[1]
    with _http_client() as client:
        response = client.get(jwks_uri)
        response.raise_for_status()
        jwks_dict = response.json()
    key_set = KeySet.import_key_set(jwks_dict)
    _jwks_cache[jwks_uri] = (now + _CACHE_TTL_SECONDS, key_set)
    return key_set


def reset_oidc_cache() -> None:
    """Test-only: force the next call to re-fetch discovery/JWKS documents."""
    _discovery_cache.clear()
    _jwks_cache.clear()


def new_code_verifier() -> str:
    """A PKCE code verifier (RFC 7636): 64 random bytes, URL-safe, 86 characters."""
    return secrets.token_urlsafe(64)


def build_authorization_url(
    connection: SsoConnection, *, redirect_uri: str, state: str, nonce: str, code_verifier: str, login_hint: str | None = None,
) -> str:
    """The provider's sign-in URL, with PKCE (S256): the code it returns is only good with
    `code_verifier`, which never leaves this server and the user's own browser, so an
    intercepted code is useless. Current OAuth guidance asks for PKCE even for a server-side
    client that also has a secret."""
    discovery = _discovery(connection)
    client = OAuth2Client(
        client_id=connection.client_id, redirect_uri=redirect_uri, scope="openid email profile", code_challenge_method="S256"
    )
    extra = {"login_hint": login_hint} if login_hint else {}
    url, _ = client.create_authorization_url(
        discovery["authorization_endpoint"], state=state, nonce=nonce, code_verifier=code_verifier, **extra
    )
    return url


def _audience_list(aud: Any) -> list[str]:
    if aud is None:
        return []
    return [aud] if isinstance(aud, str) else list(aud)


def exchange_code_for_claims(
    connection: SsoConnection, *, code: str, redirect_uri: str, expected_nonce: str, code_verifier: str | None = None
) -> OidcClaims:
    """Exchanges an authorization code at the provider's token endpoint, then verifies
    the returned id_token for real (signature against the provider's own published JWKS,
    plus iss/aud/exp/nonce) — never trusts an unverified token."""
    discovery = _discovery(connection)
    client_secret = decrypt_secret(connection.client_secret_encrypted)
    client = OAuth2Client(
        client_id=connection.client_id,
        client_secret=client_secret,
        token_endpoint_auth_method="client_secret_post",
        redirect_uri=redirect_uri,
        transport=public_only_transport(),
        timeout=get_settings().oidc_http_timeout_seconds,
    )
    try:
        token = client.fetch_token(
            discovery["token_endpoint"], code=code, **({"code_verifier": code_verifier} if code_verifier else {})
        )
    except Exception as exc:  # authlib raises its own exception hierarchy; normalize
        raise OidcError(f"Failed to exchange authorization code: {exc}") from exc

    id_token = token.get("id_token")
    if not id_token:
        raise OidcError("Identity provider did not return an id_token")

    claims = verify_provider_token(connection, id_token, kind="id_token")
    if claims.get("nonce") != expected_nonce:
        raise OidcError("id_token nonce mismatch")
    return claims_to_identity(connection, claims)


def verify_provider_token(connection: SsoConnection, token: str, *, kind: str, require_exp: bool = False) -> dict[str, Any]:
    """A JWT the identity provider signed, verified for real: signature against its own
    published JWKS, issuer, audience (our client id) and, if present (or required), expiry.
    Used for ID tokens at sign-in, back-channel logout tokens, and an online banking
    provider's ID tokens in token exchange."""
    discovery = _discovery(connection)
    try:
        jwks = _get_jwks(discovery["jwks_uri"])
    except (httpx.HTTPError, ValueError) as exc:
        raise OidcError(f"Couldn't read the provider's signing keys: {exc}") from exc
    try:
        decoded = joserfc_jwt.decode(token, jwks, algorithms=["RS256"])
    except Exception as exc:
        raise OidcError(f"{kind} signature verification failed: {exc}") from exc

    claims = decoded.claims
    expected_issuers = {discovery.get("issuer"), connection.issuer}
    if claims.get("iss") not in expected_issuers:
        raise OidcError(f"{kind} issuer mismatch")
    if connection.client_id not in _audience_list(claims.get("aud")):
        raise OidcError(f"{kind} audience mismatch")
    if claims.get("exp") is None and require_exp:
        raise OidcError(f"{kind} has no expiry")
    if claims.get("exp") is not None and float(claims["exp"]) < time.time():
        raise OidcError(f"{kind} expired")
    return dict(claims)


def claims_to_identity(connection: SsoConnection, claims: dict[str, Any]) -> OidcClaims:
    """Extracts `email`, falling back to `preferred_username` since Azure AD's v2 endpoint
    doesn't always populate `email` without extra app-registration configuration — a real
    interop caveat, not an oversight."""
    email = claims.get("email") or claims.get("preferred_username")
    if not email:
        raise OidcError("id_token did not contain an email or preferred_username claim")

    groups = claims.get(connection.groups_claim_name) or []
    if isinstance(groups, str):
        groups = [groups]

    customer_number = None
    if connection.customer_claim and claims.get(connection.customer_claim) is not None:
        customer_number = str(claims[connection.customer_claim]).strip() or None
    amr = claims.get("amr") or []
    authentication = tuple(str(v) for v in ([amr] if isinstance(amr, str) else amr)) + ((str(claims["acr"]),) if claims.get("acr") else ())
    return OidcClaims(email=email, subject=str(claims.get("sub", "")), groups=list(groups), customer_number=customer_number,
                      authentication=authentication)


BACKCHANNEL_LOGOUT_EVENT = "http://schemas.openid.net/event/backchannel-logout"
_LOGOUT_TOKEN_MAX_AGE_SECONDS = 10 * 60


def verify_logout_token(connection: SsoConnection, logout_token: str) -> dict[str, Any]:
    """An OpenID Connect Back-Channel Logout 1.0 logout token: verified like an ID token,
    plus the logout event, a `jti` (each is accepted once, by the caller), a recent `iat`,
    no `nonce`, and a `sub` (OpenClear finds people by the provider's subject; it doesn't
    track the provider's session ids)."""
    claims = verify_provider_token(connection, logout_token, kind="logout_token")
    events = claims.get("events")
    if not isinstance(events, dict) or BACKCHANNEL_LOGOUT_EVENT not in events:
        raise OidcError("logout_token has no back-channel logout event")
    if "nonce" in claims:
        raise OidcError("logout_token must not contain a nonce")
    if not claims.get("jti"):
        raise OidcError("logout_token has no jti")
    iat = claims.get("iat")
    if iat is None or abs(time.time() - float(iat)) > _LOGOUT_TOKEN_MAX_AGE_SECONDS:
        raise OidcError("logout_token is too old or from the future")
    if not claims.get("sub"):
        raise OidcError("logout_token has no sub (session-only logout isn't supported)")
    return claims
