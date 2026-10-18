# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import base64
import hashlib
import hmac
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal

import bcrypt
import jwt

from openclear.auth.keys import load_private_key, load_public_key
from openclear.config import Settings, get_settings

TokenType = Literal["access", "refresh", "mfa_pending"]

# bcrypt only reads the first 72 bytes of its input, so a long password (or a 64-character one
# in a script with multibyte letters) would be cut short. Since 1.22.1 the password is first
# HMAC-SHA-256'd and base64-encoded (44 bytes), so every character counts (ASVS 5.0 6.2.8,
# 6.2.9). The HMAC key isn't a secret: it separates these from plain SHA-256 hashes leaked
# elsewhere, without a pepper whose loss would lock everyone out. Hashes from before carry no
# prefix, verify the old way, and are replaced at the next successful sign-in.
_V2 = "ocv2:"
_PREHASH_KEY = b"openclear password prehash v2"
_LEGACY_MAX_BYTES = 72


def _prehash(plain_password: str) -> bytes:
    return base64.b64encode(hmac.new(_PREHASH_KEY, plain_password.encode("utf-8"), hashlib.sha256).digest())


def hash_password(plain_password: str) -> str:
    return _V2 + bcrypt.hashpw(_prehash(plain_password), bcrypt.gensalt()).decode("ascii")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    if hashed_password.startswith(_V2):
        return bcrypt.checkpw(_prehash(plain_password), hashed_password[len(_V2):].encode("ascii"))
    return bcrypt.checkpw(plain_password.encode("utf-8")[:_LEGACY_MAX_BYTES], hashed_password.encode("ascii"))


def password_needs_rehash(hashed_password: str) -> bool:
    """A hash from before 1.22.1, to be replaced once its password has just verified."""
    return not hashed_password.startswith(_V2)


def create_token(
    *,
    user_id: uuid.UUID,
    tenant_id: uuid.UUID,
    security_group_id: uuid.UUID,
    token_type: TokenType,
    customer_id: uuid.UUID | None = None,
    access_token_expire_minutes: int | None = None,
    refresh_token_expire_minutes: int | None = None,
    token_version: int = 0,
    session_id: uuid.UUID | None = None,
    expires_at: datetime | None = None,
    session_expires_at: datetime | None = None,
    on_behalf_of: dict | None = None,
    embed: dict | None = None,
    support_grant_id: uuid.UUID | None = None,
    confined: bool = False,
    signed_in_with: str | None = None,
    authenticated_at: int | None = None,
    settings: Settings | None = None,
) -> str:
    """The token only carries the security_group_id, not the permission set itself —
    auth/deps.py::decode_and_build_context resolves the actual permissions from the
    SecurityGroup row fresh on every request, so editing a group (or deactivating a
    membership) takes effect on the next request rather than waiting for this token to
    expire. `customer_id` is omitted (not just null) for a tenant-wide membership — the
    only kind that existed before customers did — so old tokens/callers are unaffected;
    a real value scopes the whole session to that one customer (domain/tenant_membership.py).

    `access_token_expire_minutes`/`refresh_token_expire_minutes` are the caller's
    resolved per-tenant override (Tenant.access_token_expire_minutes/
    refresh_token_expire_minutes, see services/tenant_service.py::set_session_timeouts)
    — None (the default) falls back to the global setting below. Only consulted for
    their matching token_type; mfa_pending's expiry is always the fixed global setting,
    since a bank lengthening session lifetime shouldn't also lengthen how long a WebAuthn
    ceremony has to complete.

    Signed with an ECDSA key pair (auth/keys.py), not a shared secret — see
    config.py::assert_production_safe for why this matters.

    Session claims (see create_session_tokens, which is what login/refresh call sites
    use rather than this directly): `tv` is User.token_version at mint time — bumping it
    revokes every token the user holds ("sign out everywhere"); `sid` identifies one login
    session so logout can revoke just that one (services/session_service.py); `sxp` is when
    that session hits its maximum length. `expires_at`, when given, overrides the
    lifetime computed from the expire-minutes settings.

    Keep in mind that an absent `tv` is read as 0 and an absent `sid` as "not
    individually revocable", so tokens minted before these claims existed stay valid
    until they expire rather than forcing everyone to sign in again on deploy."""
    settings = settings or get_settings()
    expire_minutes = {
        "access": access_token_expire_minutes if access_token_expire_minutes is not None else settings.jwt_access_token_expire_minutes,
        "refresh": refresh_token_expire_minutes if refresh_token_expire_minutes is not None else settings.jwt_refresh_token_expire_minutes,
        "mfa_pending": settings.mfa_pending_token_expire_minutes,
    }[token_type]
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "tenant_id": str(tenant_id),
        "security_group_id": str(security_group_id),
        "type": token_type,
        "iss": token_issuer(settings),
        "aud": SESSION_AUDIENCE,
        "iat": now,
        "exp": expires_at or now + timedelta(minutes=expire_minutes),
        "tv": token_version,
    }
    if customer_id is not None:
        payload["customer_id"] = str(customer_id)
    if session_id is not None:
        payload["sid"] = str(session_id)
    if session_expires_at is not None:
        payload["sxp"] = int(session_expires_at.timestamp())
    if on_behalf_of is not None:
        # An API client's token from token exchange (api/v1/oauth.py): it acts for this
        # person, with the permissions both hold (auth/deps.py::_act_for_exchange).
        payload["obo"] = on_behalf_of
    if embed is not None:
        # An embedded session (services/embed_service.py): `po` is the online banking origin
        # allowed to frame it. Carried through refreshes.
        payload["emb"] = embed
    if support_grant_id is not None:
        # A support session (services/support_access_service.py): built from this grant,
        # not from a membership (auth/deps.py).
        payload["sga"] = str(support_grant_id)
    if confined:
        # Started through a customer's own sign-on (CUSTOMER_ADMIN_PLAN.md, guardrail 1):
        # stays in this customer; going to another organization means signing in there.
        payload["cfd"] = 1
    if signed_in_with is not None:
        # How the session was signed in ("password" or "sso"): switching to another
        # organization checks it against what that organization allows
        # (web/routers/tenant_switch.py::switch_refusal). Carried through refreshes.
        payload["am"] = signed_in_with
    if authenticated_at is not None:
        # When the person last proved who they are (a password, a security key, SSO), as epoch
        # seconds: a sensitive change such as adding a security key needs it to be recent
        # (auth/deps.py::recently_signed_in). Carried, never renewed, by refresh and switch.
        payload["auth"] = authenticated_at
    private_key = load_private_key(settings.jwt_private_key_path)
    return jwt.encode(payload, private_key, algorithm=settings.jwt_algorithm)


@dataclass(frozen=True, slots=True)
class SessionTokens:
    access_token: str
    refresh_token: str
    session_id: uuid.UUID
    access_expires_at: datetime
    # The session's hard limit ("maximum session length"): the refresh token expires
    # here, and refreshing never extends it — only signing in again starts a new one.
    session_expires_at: datetime
    # An embedded session's claim ({"po": parent origin}); its cookies are set for a frame.
    embed: dict | None = None


# create_session_tokens' default: the person has just authenticated. Refresh, switch and
# re-issue pass the session's own time (or None, for a token from before 1.22) instead.
NEW_SIGN_IN = object()


def create_session_tokens(
    *,
    user_id: uuid.UUID,
    tenant_id: uuid.UUID,
    security_group_id: uuid.UUID,
    customer_id: uuid.UUID | None,
    token_version: int,
    access_token_expire_minutes: int | None = None,
    refresh_token_expire_minutes: int | None = None,
    session_id: uuid.UUID | None = None,
    session_expires_at: datetime | None = None,
    embed: dict | None = None,
    confined: bool = False,
    signed_in_with: str | None = None,
    authenticated_at: "int | None | object" = NEW_SIGN_IN,
    settings: Settings | None = None,
) -> SessionTokens:
    """Mints the access + refresh pair for one login session — every sign-in, refresh,
    and organization switch goes through here, so the two tokens can't drift apart on
    scope (customer_id was once missed at one call site) or session claims.

    Pass `session_id`/`session_expires_at` to continue an existing session (refresh,
    switch): the access token's lifetime is the idle timeout, capped at the session's
    end, and the refresh token keeps the session's original end — so activity keeps a
    session alive up to, never past, its maximum length. Omit both to start a new one."""
    settings = settings or get_settings()
    now = datetime.now(timezone.utc)
    access_minutes = access_token_expire_minutes if access_token_expire_minutes is not None else settings.jwt_access_token_expire_minutes
    refresh_minutes = refresh_token_expire_minutes if refresh_token_expire_minutes is not None else settings.jwt_refresh_token_expire_minutes
    session_id = session_id or uuid.uuid4()
    if authenticated_at is NEW_SIGN_IN:
        authenticated_at = int(now.timestamp())
    session_expires_at = session_expires_at or now + timedelta(minutes=refresh_minutes)
    access_expires_at = min(now + timedelta(minutes=access_minutes), session_expires_at)
    common = dict(
        user_id=user_id,
        tenant_id=tenant_id,
        security_group_id=security_group_id,
        customer_id=customer_id,
        token_version=token_version,
        session_id=session_id,
        session_expires_at=session_expires_at,
        embed=embed,
        confined=confined,
        signed_in_with=signed_in_with,
        authenticated_at=authenticated_at,
        settings=settings,
    )
    return SessionTokens(
        access_token=create_token(token_type="access", expires_at=access_expires_at, **common),
        refresh_token=create_token(token_type="refresh", expires_at=session_expires_at, **common),
        session_id=session_id,
        access_expires_at=access_expires_at,
        session_expires_at=session_expires_at,
        embed=embed,
    )


# Every token OpenClear signs names this installation as its issuer and what it's for as its
# audience (ASVS 5.0 9.2.3, 9.2.4), so a token from another installation, or signed for another
# purpose with the same key, is refused. Tokens from before 1.22.1 carry neither and are
# accepted until 1.23; the longest of them (a refresh token) has expired long before.
SESSION_AUDIENCE = "openclear-session"  # web sessions, API tokens, MFA-pending
PLATFORM_AUDIENCE = "openclear-platform"
SSO_STATE_AUDIENCE = "openclear-sso-state"


def token_issuer(settings: Settings | None = None) -> str:
    return (settings or get_settings()).webauthn_origin.rstrip("/")


def _decode(token: str, settings: Settings, audience: str, options: dict) -> dict:
    public_key = load_public_key(settings.jwt_public_key_path)
    algorithms = [settings.jwt_algorithm]
    try:
        return jwt.decode(token, public_key, algorithms=algorithms, audience=audience, issuer=token_issuer(settings),
                          options=options)
    except jwt.MissingRequiredClaimError as exc:
        if exc.claim not in ("aud", "iss"):
            raise
        payload = jwt.decode(token, public_key, algorithms=algorithms, options={**options, "verify_aud": False})
        if "aud" in payload or "iss" in payload:  # half the claims: not a pre-1.22.1 token
            raise
        return payload


def decode_token(token: str, *, verify_exp: bool = True, settings: Settings | None = None,
                 audience: str = SESSION_AUDIENCE) -> dict:
    """verify_exp=False is only for logout, which must still be able to revoke a session
    whose access token has already expired (the signature is still verified)."""
    return _decode(token, settings or get_settings(), audience, {"verify_exp": verify_exp})


_SSO_STATE_EXPIRE_MINUTES = 10


def create_sso_state_token(
    *,
    connection_id: uuid.UUID,
    tenant_id: uuid.UUID,
    nonce: str,
    next_path: str,
    state: str,
    code_verifier: str | None = None,
    settings: Settings | None = None,
) -> str:
    """Carries the SSO login-in-progress state across the redirect to the IdP and back —
    this app has no server-side session store, so (like mfa_pending) it round-trips
    through a short-lived, signed cookie instead. A dedicated `type="sso_state"` claim
    means this can never be accepted anywhere a real access/refresh/mfa_pending token is
    (auth/deps.py never checks for this type). Carrying `tenant_id` here (established
    once, at /start, when the login page's own tenant is already known) means the
    callback never needs an unscoped cross-tenant DB lookup to find it again — it can go
    straight to a normal tenant-scoped query, RLS included."""
    settings = settings or get_settings()
    now = datetime.now(timezone.utc)
    payload = {
        "connection_id": str(connection_id),
        "tenant_id": str(tenant_id),
        "nonce": nonce,
        # The OAuth `state` sent to the identity provider; the callback must get the same
        # value back (binds the callback to the browser that started the login).
        "state": state,
        "next_path": next_path,
        # The PKCE code verifier, for the token request at the callback. In an HttpOnly,
        # path-scoped cookie it stays with the browser that started the login.
        "code_verifier": code_verifier,
        "type": "sso_state",
        "iss": token_issuer(settings),
        "aud": SSO_STATE_AUDIENCE,
        "iat": now,
        "exp": now + timedelta(minutes=_SSO_STATE_EXPIRE_MINUTES),
    }
    private_key = load_private_key(settings.jwt_private_key_path)
    return jwt.encode(payload, private_key, algorithm=settings.jwt_algorithm)


def decode_sso_state_token(token: str, *, settings: Settings | None = None) -> dict:
    payload = _decode(token, settings or get_settings(), SSO_STATE_AUDIENCE, {})
    if payload.get("type") != "sso_state":
        raise jwt.InvalidTokenError("Not an sso_state token")
    return payload
