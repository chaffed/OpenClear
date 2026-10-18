# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import secrets
from urllib.parse import urlsplit
from datetime import datetime, timezone

from fastapi import Form, HTTPException, Request, Response, status

from openclear.auth.security import SessionTokens
from openclear.config import get_settings

ACCESS_COOKIE_NAME = "access_token"
REFRESH_COOKIE_NAME = "refresh_token"
MFA_COOKIE_NAME = "mfa_token"
CSRF_COOKIE_NAME = "csrf_token"
SSO_STATE_COOKIE_NAME = "sso_state"
# Purely cosmetic (web/routers/theme.py, base.html's theme toggle) — not auth-related,
# but grouped with the other cookie names here for the same reason they all are: one
# place to see every cookie this app sets.
THEME_COOKIE_NAME = "openclear_theme"
LEGACY_POSPAY_THEME_COOKIE_NAME = "pospay_theme"  # read as a fallback, so a saved theme survives the rename

# Narrow paths for the higher-privilege / narrower-purpose cookies — the browser only
# ever attaches them to requests under these paths, shrinking their exposure surface.
REFRESH_COOKIE_PATH = "/ui/auth"
MFA_COOKIE_PATH = "/ui/login/webauthn"
SSO_STATE_COOKIE_PATH = "/ui/login/sso"


# Over HTTPS every cookie carries a prefix the browser enforces (ASVS 5.0 3.3.3): __Host- for
# path-/ cookies (Secure, no Domain, so no sibling subdomain can set or overwrite them), and
# __Secure- for the narrow-path ones, which __Host- doesn't allow. Plain HTTP (local
# development) can't use either, so keeps the bare names. (1.22.1 also read the bare names over
# HTTPS, so that upgrade signed nobody out; signing out still deletes them.)
_NARROW_PATH_COOKIES = {"refresh_token", "mfa_token", "sso_state", "saml_state", "platform_session", "support_session",
                        "platform_pending"}


def cookie_name(base: str) -> str:
    if not _secure_cookies():
        return base
    return ("__Secure-" if base in _NARROW_PATH_COOKIES else "__Host-") + base


def read_cookie(request: Request, base: str) -> str | None:
    return request.cookies.get(cookie_name(base))


def is_https_deployment() -> bool:
    # Whether this deployment is actually served over HTTPS — local dev serves over plain
    # http://127.0.0.1, so this is tied to the configured origin's scheme rather than
    # hardcoded, correct in both cases without a separate "are we in prod" flag. Also used
    # by web/security_headers.py to decide whether to send Strict-Transport-Security
    # (sending it for a plain-HTTP-only deployment would be actively wrong, not just inert
    # — it tells the browser to *refuse* future plain-HTTP connections to this host).
    return get_settings().webauthn_origin.startswith("https://")


def _secure_cookies() -> bool:
    # `Secure` requires HTTPS.
    return is_https_deployment()


def _framed(embed: bool, default: str) -> dict:
    """Cookie attributes for an embedded session (services/embed_service.py). Inside a frame
    on another site, browsers only send SameSite=None cookies, which need Secure; and
    Partitioned (CHIPS) keeps them in a jar for that one online banking site, so they're
    no use to anyone else framing OpenClear. Plain-HTTP development keeps the default."""
    if embed and _secure_cookies():
        return {"samesite": "none", "partitioned": True}
    return {"samesite": default}


def _set_cookie(response: Response, name: str, value: str, *, partitioned: bool = False, **kwargs) -> None:
    """response.set_cookie, plus the Partitioned attribute, which Starlette only writes on
    Python 3.14+ (http.cookies gained it there); added to the header by hand otherwise."""
    response.set_cookie(name, value, **kwargs)
    if partitioned:
        _mark_last_cookie_partitioned(response)


def _delete_cookie(response: Response, name: str, *, partitioned: bool = False, **kwargs) -> None:
    response.delete_cookie(name, **kwargs)
    if partitioned:
        _mark_last_cookie_partitioned(response)


def _mark_last_cookie_partitioned(response: Response) -> None:
    for index in range(len(response.raw_headers) - 1, -1, -1):
        key, value = response.raw_headers[index]
        if key == b"set-cookie":
            if b"partitioned" not in value.lower():
                response.raw_headers[index] = (key, value + b"; Partitioned")
            return


def set_session_cookies(response: Response, tokens: SessionTokens) -> None:
    """Both cookies live until the SESSION's end (its maximum length), not the access
    token's: an access token past its own (idle) expiry is still sent, so
    web/deps.py::get_web_context can tell "expired, try a refresh" apart from "never
    signed in" and route through /ui/auth/resume instead of straight to login. The
    signed `exp` inside each token is what actually governs validity, and it honors the
    tenant's own timeouts (auth/security.py::create_session_tokens)."""
    max_age = max(0, int((tokens.session_expires_at - datetime.now(timezone.utc)).total_seconds()))
    _set_cookie(
        response,
        cookie_name(ACCESS_COOKIE_NAME),
        tokens.access_token,
        httponly=True,
        secure=_secure_cookies(),
        path="/",
        max_age=max_age,
        **_framed(bool(tokens.embed), "lax"),
    )
    _set_cookie(
        response,
        cookie_name(REFRESH_COOKIE_NAME),
        tokens.refresh_token,
        httponly=True,
        secure=_secure_cookies(),
        path=REFRESH_COOKIE_PATH,
        max_age=max_age,
        **_framed(bool(tokens.embed), "strict"),
    )


def set_mfa_cookie(response: Response, token: str) -> None:
    settings = get_settings()
    response.set_cookie(
        cookie_name(MFA_COOKIE_NAME),
        token,
        httponly=True,
        secure=_secure_cookies(),
        samesite="strict",
        path=MFA_COOKIE_PATH,
        max_age=settings.mfa_pending_token_expire_minutes * 60,
    )


CLEAR_SITE_DATA = '"cache", "storage"'


def clear_auth_cookies(response: Response, *, embed: bool = False) -> None:
    # The session's pages and browser storage go with it (ASVS 5.0 14.3.1): cached pages that
    # carried account data, and session.js's timing entry.
    response.headers["Clear-Site-Data"] = CLEAR_SITE_DATA
    # Attributes here must match exactly what was used when the cookie was SET, or the
    # browser silently keeps it — the most common way a "logout" button does nothing.
    response.delete_cookie(cookie_name(ACCESS_COOKIE_NAME), path="/", secure=_secure_cookies(), samesite="lax")
    if _secure_cookies():  # and the names from before 1.22.1
        response.delete_cookie(ACCESS_COOKIE_NAME, path="/", secure=True, samesite="lax")
        response.delete_cookie(REFRESH_COOKIE_NAME, path=REFRESH_COOKIE_PATH, secure=True, samesite="strict")
    response.delete_cookie(cookie_name(REFRESH_COOKIE_NAME), path=REFRESH_COOKIE_PATH, secure=_secure_cookies(), samesite="strict")
    if embed and _secure_cookies():  # a partitioned cookie is only cleared by a partitioned delete
        _delete_cookie(response, cookie_name(ACCESS_COOKIE_NAME), path="/", secure=True, samesite="none", partitioned=True)
        _delete_cookie(response, cookie_name(REFRESH_COOKIE_NAME), path=REFRESH_COOKIE_PATH, secure=True, samesite="none", partitioned=True)


def clear_mfa_cookie(response: Response) -> None:
    response.delete_cookie(cookie_name(MFA_COOKIE_NAME), path=MFA_COOKIE_PATH, secure=_secure_cookies(), samesite="strict")


def set_sso_state_cookie(response: Response, token: str) -> None:
    # Same short-lived-signed-cookie mechanism as the mfa_token cookie, needed because
    # this flow must survive a real redirect to an external identity provider and back,
    # and this app has no server-side session store (see auth/security.py's
    # create_sso_state_token).
    response.set_cookie(
        cookie_name(SSO_STATE_COOKIE_NAME),
        token,
        httponly=True,
        secure=_secure_cookies(),
        samesite="lax",  # "strict" would drop the cookie on the IdP's redirect back (a cross-site GET)
        path=SSO_STATE_COOKIE_PATH,
        max_age=10 * 60,
    )


def clear_sso_state_cookie(response: Response) -> None:
    response.delete_cookie(cookie_name(SSO_STATE_COOKIE_NAME), path=SSO_STATE_COOKIE_PATH, secure=_secure_cookies(), samesite="lax")


SAML_STATE_COOKIE_NAME = "saml_state"
SAML_STATE_COOKIE_PATH = "/ui/sso/saml"


def _saml_samesite() -> str:
    # The identity provider POSTs the answer from its own site, and a Lax cookie isn't sent
    # with a cross-site POST. None requires Secure, which needs HTTPS; plain-HTTP local
    # development falls back to Lax (fine when both run on localhost).
    return "none" if _secure_cookies() else "lax"


def set_saml_state_cookie(response: Response, token: str) -> None:
    """A SAML sign-in in progress (web/routers/auth.py::_saml_start): the request ID, where to
    go afterwards, and the RelayState it must come back with, signed. It binds the answer
    to the browser that asked, so someone else's SAML response can't be slipped into it."""
    response.set_cookie(
        cookie_name(SAML_STATE_COOKIE_NAME), token, httponly=True, secure=_secure_cookies(), samesite=_saml_samesite(),
        path=SAML_STATE_COOKIE_PATH, max_age=10 * 60,
    )


def clear_saml_state_cookie(response: Response) -> None:
    response.delete_cookie(cookie_name(SAML_STATE_COOKIE_NAME), path=SAML_STATE_COOKIE_PATH, secure=_secure_cookies(), samesite=_saml_samesite())


# --- CSRF: double-submit cookie pattern ---
#
# The existing JSON API (Authorization header) is inherently CSRF-immune — a cross-site
# page can't make the browser attach a custom header. Cookie-based auth removes that
# immunity, so every /ui/* POST needs this. The csrf_token cookie is deliberately NOT
# HttpOnly: a double-submit CSRF token's security model relies on Same-Origin Policy (a
# cross-origin attacker's JS can't read another origin's cookies), not on hiding the
# value from the page's own JS — and this page's own JS (webauthn.js) needs to read it
# to attach as a header on fetch() calls, since those carry a JSON body, not a form
# field. A cross-site attacker can trigger a request with the victim's cookies attached,
# but can't read csrf_token's value to forge the matching field/header, so a mismatch
# means the request didn't originate from a page this server rendered.
#
# base.html's logout form needs the token on EVERY authenticated page, not just pages
# with their own forms — so web/deps.py::render_template ensures it on every render
# rather than each route remembering a separate dependency. These two plain functions
# (not FastAPI dependencies) are what render_template calls.

CSRF_HEADER_NAME = "X-CSRF-Token"


def read_or_generate_csrf_token(request: Request) -> str:
    return read_cookie(request, CSRF_COOKIE_NAME) or secrets.token_urlsafe(32)


def set_csrf_cookie_if_new(request: Request, response: Response, token: str) -> None:
    if request.cookies.get(cookie_name(CSRF_COOKIE_NAME)) == token:  # the current name: an old-named cookie gets renewed
        return  # already set to this value, nothing to do
    # A browser-session cookie (no max_age). It used to expire with the access token,
    # which meant a form left open past that (a long exception review) failed CSRF on
    # submit even though the session itself was still fine.
    _set_cookie(
        response,
        cookie_name(CSRF_COOKIE_NAME),
        token,
        httponly=False,
        secure=_secure_cookies(),
        path="/",
        **_framed(bool(getattr(request.state, "embed_parent_origin", None)), "lax"),
    )


class CsrfFailed(HTTPException):
    """The form's security token is missing or doesn't match: almost always a form left open
    so long that its token expired, or a page opened before signing in again. main.py shows
    a page that says so."""

    def __init__(self) -> None:
        super().__init__(status.HTTP_403_FORBIDDEN, "Invalid or missing CSRF token")


def verify_csrf(request: Request, csrf_token: str = Form("")) -> None:
    """Dependency for classic HTML-form /ui/* POST routes. Compares the submitted hidden
    field against the cookie value using a constant-time comparison."""
    cookie_value = read_cookie(request, CSRF_COOKIE_NAME)
    if not cookie_value or not csrf_token or not secrets.compare_digest(cookie_value, csrf_token):
        raise CsrfFailed()


def verify_csrf_header(request: Request) -> None:
    """Dependency for JSON/fetch-based /ui/* POST routes (WebAuthn options/verify,
    credential deletion) — same check, read from the X-CSRF-Token header instead of a
    form field since these routes take a JSON body."""
    cookie_value = read_cookie(request, CSRF_COOKIE_NAME)
    header_value = request.headers.get(CSRF_HEADER_NAME)
    if not cookie_value or not header_value or not secrets.compare_digest(cookie_value, header_value):
        raise CsrfFailed()


def safe_next_path(path: str | None, *, default: str = "/ui/") -> str:
    """Guards against open-redirect: a `next` value must be a same-origin relative path
    (a single leading '/', never '//...' which browsers treat as protocol-relative to an
    external host) — otherwise login could be used to bounce a user to an attacker's site."""
    if not path or not path.startswith("/") or path.startswith("//"):
        return default
    # Browsers read "/\\evil.com" as "//evil.com", and strip tabs and newlines first: refuse
    # backslashes and control characters, and anything that parses with a host or scheme.
    if "\\" in path or any(ord(ch) < 32 or ord(ch) == 127 for ch in path):
        return default
    parts = urlsplit(path)
    if parts.scheme or parts.netloc:
        return default
    return path


def was_embedded(request: Request) -> bool:
    """Whether the browser's session cookies, valid or not, belong to an embedded session
    (services/embed_service.py): where a frame should land when that session ends, instead
    of a login page it can't show. Only the signature is checked, not expiry."""
    from openclear.auth.security import decode_token

    for name in (ACCESS_COOKIE_NAME,):
        token = read_cookie(request, name)
        if token:
            try:
                return bool(decode_token(token, verify_exp=False).get("emb"))
            except Exception:  # noqa: BLE001 -- not ours, or garbled: not embedded
                return False
    return False
