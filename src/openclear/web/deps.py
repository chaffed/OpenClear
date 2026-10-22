# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

from urllib.parse import urlsplit

import jwt
from fastapi import Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from openclear.auth.deps import AccessRevoked, WrongTokenType, decode_and_build_context
from openclear.db.session import get_db
from openclear.web.demo_guard import DEMO_LOCKED_MESSAGE, is_locked_in_demo
from openclear.db.tenancy import TenantContext
from openclear.web.security import (
    read_cookie,
    read_or_generate_csrf_token,
    set_csrf_cookie_if_new,
    ACCESS_COOKIE_NAME,
    LEGACY_POSPAY_THEME_COOKIE_NAME,
    MFA_COOKIE_NAME,
    THEME_COOKIE_NAME,
)
from openclear.web.templates import templates


# The one page a must_change_password session may reach (web/routers/security_settings.py).
PASSWORD_CHANGE_PATH = "/ui/security/password"


class WebAuthRequired(Exception):
    """Raised instead of a JSON 401 anywhere in /ui/* — a browser needs a redirect to the
    login page, not a JSON error body. Caught by an exception handler registered in
    main.py that returns a RedirectResponse. `next_path` round-trips through the login
    form so a successful login can send the user back where they were headed."""

    def __init__(self, next_path: str | None = None, *, try_resume: bool = False):
        self.next_path = next_path
        # True when the access token merely expired (its signature was fine): main.py's
        # handler then goes through /ui/auth/resume, which silently continues the session
        # if its refresh token is still good, instead of straight to the login form.
        self.try_resume = try_resume


class WebForbidden(Exception):
    """Raised when a role lacks a permission for a /ui/* route — rendered as an HTML
    error page (not a JSON 403) by an exception handler in main.py. `message`, when
    given, replaces the generic "You don't have permission" text (e.g. the demo lock)."""

    def __init__(self, message: str | None = None, *, title: str | None = None):
        super().__init__(message)
        self.message = message
        self.title = title


class WebPasswordChangeRequired(Exception):
    """Raised by get_web_context for a session whose user must replace an admin-issued
    temporary password — main.py's handler redirects to the change-password page, the one
    /ui/* page such a session may use (logout needs no context, so it still works too)."""


class WebNotFound(Exception):
    """Raised when a /ui/* route's resource id doesn't resolve to a row this tenant owns
    (either truly missing, or belonging to another tenant — same outward response either
    way, so this never leaks which). Rendered as an HTML 404 page in main.py."""


def _return_path(request: Request) -> str | None:
    """Where to send the user once they're signed in again. For a GET, the page itself
    (query string included, so filters survive). For a form POST, the page the form was
    on, taken from a same-origin Referer: re-requesting the POST's own URL as a GET after
    sign-in would just fail, and what they'd typed is gone either way."""
    if request.method == "GET":
        return request.url.path + (f"?{request.url.query}" if request.url.query else "")
    referer = urlsplit(request.headers.get("referer", ""))
    if referer.netloc and referer.netloc == request.url.netloc:
        return referer.path + (f"?{referer.query}" if referer.query else "")
    return None


def get_web_context(request: Request, db: Session = Depends(get_db)) -> TenantContext:
    token = read_cookie(request, ACCESS_COOKIE_NAME)
    if not token:
        raise WebAuthRequired(next_path=_return_path(request))
    try:
        ctx = decode_and_build_context(token, db, expected_type="access")
    except jwt.ExpiredSignatureError:
        raise WebAuthRequired(next_path=_return_path(request), try_resume=True) from None
    except (jwt.InvalidTokenError, WrongTokenType, AccessRevoked):
        # Garbled, logged out, signed out everywhere, or an access/membership deactivated
        # since the token was issued: back to login.
        raise WebAuthRequired(next_path=_return_path(request)) from None
    if ctx.must_change_password and request.url.path != PASSWORD_CHANGE_PATH:
        raise WebPasswordChangeRequired()
    ctx = _navigation_counts(db, ctx)
    if "exception:read" in ctx.permissions:
        import dataclasses

        from openclear.services import exception_service

        count, nearest = exception_service.attention_summary(db, ctx.tenant_id, customer_id=ctx.customer_id)
        ctx = dataclasses.replace(ctx, needs_attention_count=count, nearest_deadline=nearest)
    if ctx.customer_id is None and "customer:manage" in ctx.permissions:
        import dataclasses

        from openclear.services import customer_approval_service

        ctx = dataclasses.replace(ctx, customer_users_waiting=customer_approval_service.waiting_count(db, ctx.tenant_id))
    if ctx.is_demo and is_locked_in_demo(request.method, request.url.path):
        raise WebForbidden(DEMO_LOCKED_MESSAGE, title="Turned off in the demo")
    if ctx.embed_parent_origin:
        # Framing is allowed for this session's pages, by its online banking origin, while
        # the bank still allows that origin (security_headers.py).
        from openclear.services import embed_service

        request.state.embed_parent_origin = ctx.embed_parent_origin
        request.state.frame_ancestor = (
            ctx.embed_parent_origin if embed_service.origin_allowed(db, ctx.tenant_id, ctx.embed_parent_origin) else None
        )
    return ctx


def _navigation_counts(db: Session, ctx: TenantContext) -> TenantContext:
    """What only the navigation shows: approvals waiting on this person, and whether they belong
    to another organization. Worked out for web pages only, not for every API request."""
    import dataclasses

    from openclear.auth.deps import _pending_approval_count
    from openclear.db.tenancy import memberships_elsewhere
    from openclear.domain.user import User

    user = db.get(User, ctx.user_id)
    elsewhere = 0 if ctx.support_grant_id or (user is not None and user.is_service_account) else memberships_elsewhere(db, ctx.user_id, ctx.tenant_id)
    pending = _pending_approval_count(db, ctx.tenant_id, ctx.customer_id, ctx.user_id) if "exception:decide" in ctx.permissions else 0
    return dataclasses.replace(ctx, other_organizations=elsewhere, pending_approval_count=pending)


def get_mfa_pending_web_context(request: Request, db: Session = Depends(get_db)) -> TenantContext:
    """For /ui/login/webauthn/* only — mirrors auth.get_mfa_pending_context but reads the
    mfa_token cookie instead of an Authorization header (see web/security.py)."""
    token = read_cookie(request, MFA_COOKIE_NAME)
    if not token:
        raise WebAuthRequired(next_path="/ui/login")
    try:
        return decode_and_build_context(token, db, expected_type="mfa_pending")
    except (jwt.ExpiredSignatureError, jwt.InvalidTokenError, WrongTokenType, AccessRevoked):
        raise WebAuthRequired(next_path="/ui/login") from None


def require_web_permission(permission: str):
    def _check(ctx: TenantContext = Depends(get_web_context)) -> TenantContext:
        if permission not in ctx.permissions:
            from openclear.auth.deps import _refused

            _refused("access.denied", ctx, permission, key=permission)
            raise WebForbidden()
        return ctx

    return _check


def require_any_web_permission(*permissions: str):
    """Like require_web_permission, but passes if ANY of the given permissions is held —
    for a page shared by two otherwise-unrelated audiences (e.g. /ui/admin, reachable by
    either admin:manage or tenant:manage holders, each seeing only their own section)."""

    def _check(ctx: TenantContext = Depends(get_web_context)) -> TenantContext:
        if not any(p in ctx.permissions for p in permissions):
            raise WebForbidden()
        return ctx

    return _check


def render_template(
    request: Request,
    name: str,
    *,
    ctx: TenantContext | None = None,
    status_code: int = 200,
    **extra,
) -> HTMLResponse:
    """Every /ui/* route renders through this so `request`, `ctx` (base.html's role-aware
    nav), `csrf_token` (base.html's logout form, and any page-specific form), and `theme`
    (base.html's data-theme attribute + theme toggle, set by web/routers/theme.py) are
    never forgotten — the one thing every template needs, regardless of what page-specific
    data it also needs."""
    csrf_token = read_or_generate_csrf_token(request)
    theme = read_cookie(request, THEME_COOKIE_NAME) or request.cookies.get(LEGACY_POSPAY_THEME_COOKIE_NAME)
    context = {"ctx": ctx, "csrf_token": csrf_token, "theme": theme, **extra}
    response = templates.TemplateResponse(request, name, context, status_code=status_code)
    set_csrf_cookie_if_new(request, response, csrf_token)
    return response
