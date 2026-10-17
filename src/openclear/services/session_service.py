# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Server-side control over otherwise-stateless signed tokens.

- One session (logout): its `sid` goes into RevokedSession (domain/revoked_session.py).
- Every session a user has, on every device (password change/reset, an admin's "Sign out
  everywhere"): User.token_version is incremented, so every outstanding token's `tv`
  claim stops matching.
- Refresh (web keep-alive and POST /api/v1/auth/refresh alike): re-validates everything
  an access token is checked against, then continues the same session — see
  auth/security.py::create_session_tokens for how its maximum length is kept.

auth/deps.py::decode_and_build_context applies the same tv/sid checks to every request."""

import uuid
from datetime import datetime, timedelta, timezone

import jwt
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from openclear.auth.security import SessionTokens, create_session_tokens, decode_token
from openclear.config import get_settings
from openclear.domain.revoked_session import RevokedSession
from openclear.domain.tenant import Tenant
from openclear.domain.tenant_membership import TenantMembership
from openclear.domain.user import User


class RefreshFailed(Exception):
    """The refresh token is invalid, expired, revoked, or no longer backed by an active
    user/membership. Callers treat every reason the same way (sign in again)."""


def is_session_revoked(session: Session, session_id: uuid.UUID) -> bool:
    return session.get(RevokedSession, session_id) is not None


def claims_are_current(session: Session, claims: dict, user: User) -> bool:
    """The tv/sid half of token validation, shared by decode_and_build_context and
    refresh_session so the two can't drift."""
    if claims.get("tv", 0) != user.token_version:
        return False
    sid = claims.get("sid")
    return not (sid and is_session_revoked(session, uuid.UUID(sid)))


def revoke_session(session: Session, *, session_id: uuid.UUID, user_id: uuid.UUID) -> None:
    """Ends one login session. Idempotent. The row only has to outlive the longest any
    session could still be valid — the platform default or the longest per-tenant
    override, whichever is greater. Expired rows are pruned opportunistically here rather
    than by a scheduled job."""
    now = datetime.now(timezone.utc)
    session.execute(delete(RevokedSession).where(RevokedSession.expires_at < now))
    if session.get(RevokedSession, session_id) is None:
        keep_minutes = max(get_settings().jwt_refresh_token_expire_minutes, _longest_tenant_session_minutes(session))
        session.add(RevokedSession(session_id=session_id, user_id=user_id, expires_at=now + timedelta(minutes=keep_minutes)))
    session.flush()


def _longest_tenant_session_minutes(session: Session) -> int:
    return session.execute(select(func.max(Tenant.refresh_token_expire_minutes))).scalar() or 0


def revoke_all_sessions(session: Session, user: User) -> None:
    """Signs the user out everywhere — every token they hold, in every organization, on
    every device, stops working on its next use."""
    user.token_version += 1
    session.flush()


def refresh_session(session: Session, refresh_token: str) -> tuple[SessionTokens, Tenant]:
    """Validates a refresh token and continues its session with a fresh token pair.
    Group, tenant session-timeout settings, and scope are re-read from the database, not
    trusted from the old token."""
    try:
        claims = decode_token(refresh_token)
    except jwt.InvalidTokenError:
        raise RefreshFailed("Invalid or expired refresh token") from None
    if claims.get("type") != "refresh":
        raise RefreshFailed("Not a refresh token")

    user = session.get(User, uuid.UUID(claims["sub"]))
    if user is None or not user.is_active:
        raise RefreshFailed("User no longer active")
    if not claims_are_current(session, claims, user):
        raise RefreshFailed("Session was signed out")

    tenant_id = uuid.UUID(claims["tenant_id"])
    customer_id = uuid.UUID(claims["customer_id"]) if claims.get("customer_id") else None
    membership = session.execute(
        select(TenantMembership).where(
            TenantMembership.user_id == user.id,
            TenantMembership.tenant_id == tenant_id,
            TenantMembership.customer_id == customer_id,
        )
    ).scalar_one_or_none()
    if membership is None or not membership.is_active:
        raise RefreshFailed("Membership no longer active")
    tenant = session.get(Tenant, tenant_id)
    if tenant is None or not tenant.is_active:
        raise RefreshFailed("Organization no longer active")

    # A pre-sid refresh token (minted before session claims existed) starts a fresh
    # session id; its maximum length is still its own expiry.
    session_id = uuid.UUID(claims["sid"]) if claims.get("sid") else None
    tokens = create_session_tokens(
        user_id=user.id,
        tenant_id=tenant_id,
        security_group_id=membership.security_group_id,
        customer_id=customer_id,
        token_version=user.token_version,
        access_token_expire_minutes=tenant.access_token_expire_minutes,
        refresh_token_expire_minutes=tenant.refresh_token_expire_minutes,
        session_id=session_id,
        session_expires_at=datetime.fromtimestamp(claims["exp"], tz=timezone.utc),
        embed=claims.get("emb"),  # an embedded session stays one
        confined=bool(claims.get("cfd")),  # and a confined one stays confined
        signed_in_with=claims.get("am"),
    )
    return tokens, tenant
