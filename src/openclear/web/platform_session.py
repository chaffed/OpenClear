# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""The platform staff's own session (UX_PLAN.md U8, U9): a signed token in its own cookie,
separate from any bank session, for the operator view and a support person's list of
grants. Platform staff are users with a platform_role (scripts/platform_staff.py)."""

import uuid
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Depends, Request
from sqlalchemy.orm import Session

from openclear.auth.keys import load_private_key
from openclear.auth.security import PLATFORM_AUDIENCE, decode_token, token_issuer
from openclear.config import get_settings
from openclear.db.session import get_db
from openclear.domain.user import User

COOKIE = "platform_session"
LIFETIME = timedelta(hours=8)


class PlatformAuthRequired(Exception):
    """Redirects to the platform sign-in page (handled in main.py)."""


class PlatformForbidden(Exception):
    """Signed in, but this page is for another platform role."""


def create_token(user: User) -> str:
    settings = get_settings()
    now = datetime.now(timezone.utc)
    payload = {"sub": str(user.id), "type": "platform", "role": user.platform_role, "tv": user.token_version,
               "iss": token_issuer(settings), "aud": PLATFORM_AUDIENCE, "iat": now, "exp": now + LIFETIME}
    return jwt.encode(payload, load_private_key(settings.jwt_private_key_path), algorithm=settings.jwt_algorithm)


def set_cookie(response, token: str) -> None:
    from openclear.web.security import _secure_cookies, cookie_name

    response.set_cookie(cookie_name(COOKIE), token, httponly=True, secure=_secure_cookies(), samesite="strict",
                        max_age=int(LIFETIME.total_seconds()), path="/ui")


def clear_cookie(response) -> None:
    from openclear.web.security import _secure_cookies, cookie_name

    response.delete_cookie(cookie_name(COOKIE), path="/ui", secure=_secure_cookies(), samesite="strict")


def set_support_session(response, token: str, expires_at: datetime) -> None:
    """A support session replaces any bank session in this browser: its own access token,
    good until the grant ends, and no refresh token (it can't outlive the grant)."""
    from openclear.web.security import ACCESS_COOKIE_NAME, _secure_cookies, clear_auth_cookies, cookie_name

    clear_auth_cookies(response)
    max_age = max(0, int((expires_at - datetime.now(timezone.utc)).total_seconds()))
    response.set_cookie(cookie_name(ACCESS_COOKIE_NAME), token, httponly=True, secure=_secure_cookies(), samesite="lax", path="/", max_age=max_age)


def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    from openclear.web.security import read_cookie

    token = read_cookie(request, COOKIE)
    if not token:
        raise PlatformAuthRequired()
    try:
        payload = decode_token(token, audience=PLATFORM_AUDIENCE)
    except jwt.PyJWTError:
        raise PlatformAuthRequired() from None
    if payload.get("type") != "platform":
        raise PlatformAuthRequired()
    user = db.get(User, uuid.UUID(payload["sub"]))
    if user is None or not user.is_active or user.platform_role is None or payload.get("tv", 0) != user.token_version:
        raise PlatformAuthRequired()
    from openclear.web import request_context

    request_context.note_identity(tenant_id=None, tenant_slug=None, user_id=user.id, user_email=user.email)
    return user


def require_operator(user: User = Depends(current_user)) -> User:
    if user.platform_role != "operator":
        raise PlatformForbidden()
    return user
