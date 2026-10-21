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
    session.execute(delete(RevokedSession).where(RevokedSession.expires_at < now).execution_options(synchronize_session=False))
    if session.get(RevokedSession, session_id) is None:
        keep_minutes = max(get_settings().jwt_refresh_token_expire_minutes, _longest_tenant_session_minutes(session))
        session.add(RevokedSession(session_id=session_id, user_id=user_id, expires_at=now + timedelta(minutes=keep_minutes)))
    from openclear.domain.login_session import LoginSession

    row = session.get(LoginSession, session_id)
    if row is not None and row.ended_at is None:
        row.ended_at = now
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
    if claims.get("rid"):
        _retire_refresh_token(session, uuid.UUID(claims["rid"]), user, session_id, tenant_id,
                              datetime.fromtimestamp(claims["exp"], tz=timezone.utc))
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
        authenticated_at=claims.get("auth"),
    )
    return tokens, tenant


# Two tabs refreshing at once both send the token they hold; the second, a moment after the
# first retired it, isn't a thief.
REUSE_GRACE = timedelta(seconds=60)


def _retire_refresh_token(session: Session, refresh_id: uuid.UUID, user: User, session_id: uuid.UUID | None,
                          tenant_id: uuid.UUID, expires_at: datetime) -> None:
    """Refresh tokens are used once (FULL_REVIEW_PLAN.md FR-D8; ASVS 5.0 7.2.4): each refresh
    retires the token it was given, recorded like a revoked session (keyed by the token's own
    `rid`, kept until it would have expired). A retired token presented again after REUSE_GRACE
    was copied: the session ends for whoever holds it, and the bank's security team is told.
    The caller commits."""
    from sqlalchemy.exc import IntegrityError

    from openclear.services import security_events

    now = datetime.now(timezone.utc)
    retired = session.get(RevokedSession, refresh_id)
    if retired is None:
        session.add(RevokedSession(session_id=refresh_id, user_id=user.id, revoked_at=now, expires_at=expires_at))
        try:
            session.flush()
        except IntegrityError:  # retired by a refresh running alongside this one: the grace case
            session.rollback()
        return
    revoked_at = retired.revoked_at if retired.revoked_at.tzinfo else retired.revoked_at.replace(tzinfo=timezone.utc)
    if now - revoked_at <= REUSE_GRACE:
        return
    if session_id is not None:
        revoke_session(session, session_id=session_id, user_id=user.id)
    security_events.emit(session, "auth.session.reuse_detected", tenant_id=tenant_id, outcome="failure", reason="refresh_token_reused",
                         actor_type="user", actor_id=user.id, actor_label=user.email,
                         details={"session_id": str(session_id) if session_id else None})
    session.commit()  # the session stays ended though the refresh is refused
    raise RefreshFailed("This sign-in was used from somewhere else, so it was ended. Sign in again.")


# --- The sessions list (OWASP_PLAN.md OW-D29) --------------------------------------------------

_SEEN_EVERY = timedelta(minutes=5)
SESSION_ROWS_KEPT_DAYS = 30


def note_session(db: Session, *, session_id: uuid.UUID, tenant_id: uuid.UUID, user: User, signed_in_with: str | None,
                 expires_at: datetime | None) -> None:
    """Records the session on its first request, and when it was last used at most every five
    minutes, for the sessions list. Written in its own short transaction: many requests never
    commit, and the record mustn't depend on them."""
    from sqlalchemy.orm import Session as OrmSession

    from openclear.db.tenancy import bind_session_to_tenant
    from openclear.domain.login_session import LoginSession
    from openclear.web import request_context

    now = datetime.now(timezone.utc)
    context = request_context.current()
    with OrmSession(bind=db.get_bind()) as own:
        bind_session_to_tenant(own, tenant_id)
        row = own.get(LoginSession, session_id)
        if row is None:
            own.add(LoginSession(id=session_id, tenant_id=tenant_id, user_id=user.id, signed_in_with=signed_in_with,
                                 token_version=user.token_version, user_agent=context.user_agent if context else None,
                                 ip_address=context.client_ip if context else None, started_at=now, last_seen_at=now,
                                 expires_at=expires_at))
        else:
            last = row.last_seen_at if row.last_seen_at.tzinfo else row.last_seen_at.replace(tzinfo=timezone.utc)
            if now - last < _SEEN_EVERY:
                return
            row.last_seen_at = now
            if context and context.client_ip:
                row.ip_address = context.client_ip
        try:
            own.commit()
        except Exception:  # two first requests at once: the other one recorded it
            own.rollback()


def active_sessions(db: Session, tenant_id: uuid.UUID, user_ids) -> list:
    """The listed people's sessions at this bank that still work: not ended, not past their end,
    and not from before their last "Sign out everywhere"."""
    from openclear.domain.login_session import LoginSession

    ids = list(user_ids)
    if not ids:
        return []
    now = datetime.now(timezone.utc)
    versions = dict(db.execute(select(User.id, User.token_version).where(User.id.in_(ids))).all())
    revoked = set(db.execute(select(RevokedSession.session_id).where(RevokedSession.user_id.in_(ids))).scalars())
    rows = db.execute(select(LoginSession).where(LoginSession.tenant_id == tenant_id, LoginSession.user_id.in_(ids),
                                                 LoginSession.ended_at.is_(None))
                      .order_by(LoginSession.last_seen_at.desc())).scalars().all()

    def live(row) -> bool:
        ends = row.expires_at if (row.expires_at is None or row.expires_at.tzinfo) else row.expires_at.replace(tzinfo=timezone.utc)
        return row.id not in revoked and row.token_version == versions.get(row.user_id) and (ends is None or ends > now)

    return [r for r in rows if live(r)]


def end_sessions(db: Session, rows, *, keep: uuid.UUID | None = None) -> int:
    """Ends each listed session (except `keep`, the caller's own). Returns how many."""
    ended = 0
    for row in rows:
        if row.id != keep:
            revoke_session(db, session_id=row.id, user_id=row.user_id)
            ended += 1
    return ended


def describe_browser(user_agent: str | None) -> str:
    """"Chrome on macOS" from a user agent, for the sessions list; good enough to recognise one's
    own devices, not a parser."""
    ua = user_agent or ""
    browser = next((name for key, name in (("Edg/", "Edge"), ("OPR/", "Opera"), ("Firefox/", "Firefox"), ("Chrome/", "Chrome"),
                                           ("Safari/", "Safari")) if key in ua), "A browser")
    system = next((name for key, name in (("iPhone", "iPhone"), ("iPad", "iPad"), ("Android", "Android"), ("Windows", "Windows"),
                                          ("Mac OS X", "macOS"), ("Linux", "Linux")) if key in ua), "")
    if not ua:
        return "Unknown"
    if "python-httpx" in ua or "curl/" in ua or "OpenClear" in ua:
        return "A program (API)"
    return f"{browser} on {system}" if system else browser
