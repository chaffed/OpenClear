# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

from fastapi import APIRouter, Depends, Form
from fastapi.responses import RedirectResponse

from openclear.web.security import THEME_COOKIE_NAME, _secure_cookies, cookie_name, safe_next_path, verify_csrf
from openclear.web.redirects import see_other

router = APIRouter(prefix="/ui/theme", tags=["web-theme"])

_VALID_THEMES = {"light", "dark"}


@router.post("")
def set_theme(
    theme: str = Form(...),
    next: str | None = Form(None),
    _csrf: None = Depends(verify_csrf),
) -> RedirectResponse:
    """A purely cosmetic, no-auth-required preference — works identically pre- and
    post-login (base.html renders this control on every page, including auth/login.html)
    since it's a plain cookie read directly in web/deps.py::render_template, not tied to
    a TenantContext or User row. `theme=system` (the default/unset state) clears any
    previous override so the page falls back to the `@media (prefers-color-scheme: dark)`
    CSS rule in static/css/app.css."""
    response = see_other(safe_next_path(next))
    if theme in _VALID_THEMES:
        response.set_cookie(
            cookie_name(THEME_COOKIE_NAME),
            theme,
            httponly=True,
            secure=_secure_cookies(),
            samesite="lax",
            path="/",
            max_age=365 * 24 * 60 * 60,
        )
    else:
        response.delete_cookie(cookie_name(THEME_COOKIE_NAME), path="/", secure=_secure_cookies(), samesite="lax")
    return response
