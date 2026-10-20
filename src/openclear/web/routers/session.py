# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Keeping a web session alive. Both routes live under /ui/auth because the refresh cookie
is path-scoped there (web/security.py::REFRESH_COOKIE_PATH) — no other route ever sees it.

- POST /ui/auth/refresh: called by static/js/session.js while the user is active (and by
  its "Stay signed in" button), a few minutes before the access token's idle timeout.
- GET /ui/auth/resume?next=...: where web/deps.py::get_web_context sends a page request
  whose access token has expired. If it expired only moments ago (see _IDLE_GRACE), the
  session continues and the user lands on the page they asked for; otherwise they sign in
  again and are then sent there. A GET is safe here: it changes no data, it only reissues
  the caller's own session from a cookie a cross-site request can't read, and `next` is
  restricted to a same-origin path.

The idle timeout is enforced HERE, server-side: the web UI may only continue a session
whose access token hasn't yet expired (give or take _IDLE_GRACE). Without that, a refresh
token alone could revive a session that had sat idle for hours, right up to its maximum
length, and the tenant's idle-timeout setting would mean nothing. (The JSON API keeps
normal OAuth-style refresh semantics: clients refresh after expiry, by design.)"""

from datetime import datetime, timedelta, timezone

import jwt
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy.orm import Session

from openclear.auth.security import decode_token
from openclear.db.session import get_db
from openclear.services import session_service
from openclear.web.security import (
    clear_auth_cookies,
    read_cookie,
    safe_next_path,
    set_session_cookies,
    verify_csrf_header,
    was_embedded,
    ACCESS_COOKIE_NAME,
    REFRESH_COOKIE_NAME,
)
from openclear.web.redirects import see_other

router = APIRouter(prefix="/ui/auth", tags=["web-session"])

# Slack for timers browsers throttle in background tabs, so a keep-alive that fires a
# little late doesn't sign out someone who was actually active.
_IDLE_GRACE = timedelta(minutes=2)


def _idle_timeout_not_reached(request: Request) -> bool:
    token = read_cookie(request, ACCESS_COOKIE_NAME)
    if not token:
        return False
    try:
        claims = decode_token(token, verify_exp=False)
    except jwt.InvalidTokenError:
        return False
    return datetime.fromtimestamp(claims["exp"], tz=timezone.utc) + _IDLE_GRACE >= datetime.now(timezone.utc)


def _continue_session(request: Request, db: Session):
    if not _idle_timeout_not_reached(request):
        raise session_service.RefreshFailed("Signed out after a period of inactivity")
    tokens, _tenant = session_service.refresh_session(db, read_cookie(request, REFRESH_COOKIE_NAME) or "")
    return tokens


@router.post("/refresh")
def refresh(request: Request, db: Session = Depends(get_db), _csrf: None = Depends(verify_csrf_header)) -> JSONResponse:
    try:
        tokens = _continue_session(request, db)
    except session_service.RefreshFailed:
        response = JSONResponse({"error": "Your session has ended. Please sign in again."}, status_code=401)
        clear_auth_cookies(response, embed=was_embedded(request))
        return response
    response = JSONResponse(
        {
            "access_expires_at": int(tokens.access_expires_at.timestamp()),
            "session_expires_at": int(tokens.session_expires_at.timestamp()),
        }
    )
    set_session_cookies(response, tokens)
    return response


@router.get("/resume")
def resume(request: Request, next: str | None = None, db: Session = Depends(get_db)) -> RedirectResponse:
    next_path = safe_next_path(next)
    try:
        tokens = _continue_session(request, db)
    except session_service.RefreshFailed:
        embedded = was_embedded(request)
        if embedded:
            response = see_other("/ui/embed/ended")
        else:
            response = see_other("/ui/login", next=None if next_path == "/ui/" else next_path)
        clear_auth_cookies(response, embed=embedded)
        return response
    response = see_other(next_path)
    set_session_cookies(response, tokens)
    return response
