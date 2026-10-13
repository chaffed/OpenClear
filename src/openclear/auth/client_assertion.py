# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""API clients that sign in with a key pair instead of a secret: `private_key_jwt` (RFC 7523,
section 2.2, as OpenID Connect uses it). The bank registers the client's public key; the
client signs a short-lived assertion with its private key, which never leaves its own systems.

The assertion is a JWT with:
- `iss` and `sub`: the client id;
- `aud`: the token endpoint's URL;
- `exp` at most five minutes after `iat`, and `jti`: a unique id, accepted once.

Signed with RS256/384/512, PS256/384/512 (RSA, 2048 bits or more) or ES256/384 (P-256, P-384)."""

import uuid
from datetime import datetime, timedelta, timezone

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

ASSERTION_TYPE = "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"
ALGORITHMS = ["RS256", "RS384", "RS512", "PS256", "PS384", "PS512", "ES256", "ES384"]
MAX_LIFETIME = timedelta(minutes=5)
LEEWAY_SECONDS = 30


class UnusableKey(ValueError):
    """A public key that can't be used; the message is fit to show."""


def load_public_key(pem: str):
    """The public key from PEM text, if it's one we accept. Raises UnusableKey."""
    try:
        key = serialization.load_pem_public_key((pem or "").strip().encode())
    except (ValueError, TypeError):
        raise UnusableKey("That isn't a PEM public key (-----BEGIN PUBLIC KEY-----).") from None
    if isinstance(key, rsa.RSAPublicKey):
        if key.key_size < 2048:
            raise UnusableKey("An RSA key must be at least 2048 bits.")
    elif isinstance(key, ec.EllipticCurvePublicKey):
        if key.curve.name not in ("secp256r1", "secp384r1"):
            raise UnusableKey("An elliptic-curve key must be P-256 or P-384.")
    else:
        raise UnusableKey("Use an RSA or elliptic-curve (P-256, P-384) key.")
    return key


def verify(session: Session, assertion: str, *, audiences: list[str]):
    """The API client this assertion signs in, or None. Binds the session to the client's
    bank. Records the assertion's jti, so it can't be used again; the caller commits."""
    from openclear.db.tenancy import bind_session_to_tenant
    from openclear.domain.api_client import ApiClient, ClientAssertionUse
    from openclear.domain.tenant import Tenant
    from openclear.domain.tenant_membership import TenantMembership
    from openclear.domain.user import User
    from openclear.services.api_client_service import tenant_from_client_id

    try:
        unverified = jwt.decode(assertion, options={"verify_signature": False})
    except jwt.PyJWTError:
        return None
    client_id = str(unverified.get("iss") or "")
    tenant_id = tenant_from_client_id(client_id)
    if tenant_id is None:
        return None
    tenant = session.get(Tenant, tenant_id)
    if tenant is None or not tenant.is_active:
        return None
    bind_session_to_tenant(session, tenant_id)
    client = session.execute(select(ApiClient).where(ApiClient.client_id == client_id)).scalar_one_or_none()
    if client is None or client.revoked_at is not None or client.token_auth_method != "private_key_jwt" or not client.public_key_pem:
        return None
    try:
        claims = jwt.decode(
            assertion, load_public_key(client.public_key_pem), algorithms=ALGORITHMS, audience=audiences, issuer=client_id,
            leeway=LEEWAY_SECONDS, options={"require": ["iss", "sub", "aud", "exp", "iat", "jti"]},
        )
    except (jwt.PyJWTError, UnusableKey):
        return None
    if claims["sub"] != client_id:
        return None
    issued = datetime.fromtimestamp(claims["iat"], tz=timezone.utc)
    expires = datetime.fromtimestamp(claims["exp"], tz=timezone.utc)
    if expires - issued > MAX_LIFETIME or expires > datetime.now(timezone.utc) + MAX_LIFETIME:
        return None
    membership = session.get(TenantMembership, client.membership_id)
    user = session.get(User, client.user_id)
    if membership is None or not membership.is_active or user is None or not user.is_active:
        return None
    try:
        with session.begin_nested():
            session.add(ClientAssertionUse(tenant_id=tenant_id, api_client_id=client.id, jti=str(claims["jti"])[:200], expires_at=expires))
    except IntegrityError:
        return None  # used before: a replay
    client.last_used_at = datetime.now(timezone.utc)
    return client


def new_jti() -> str:
    return uuid.uuid4().hex
