# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Security response headers, applied to every response by main.py's middleware.

Content-Security-Policy is the one that needs real justification, not just a checklist
entry, since this app renders user-authored content (sanitized Markdown banner/message
text — services/message_content.py, web/templates.py::render_markdown): script-src is
kept STRICT ('self' plus a fresh per-request nonce, no 'unsafe-inline') because that's the
one header that would actually stop an injected <script> from executing if a future bug
ever slipped past nh3 sanitization — a real, not theoretical, defense-in-depth layer given
what this app renders. Getting there required a small refactor: this codebase had a
handful of inline <script> blocks (now nonce'd, see login_webauthn.html /
login_webauthn_setup.html / security/webauthn.html) and inline onchange=".../onclick="..."
handlers (now data-auto-submit / data-confirm attributes, wired up in app.js) that a
strict script-src would otherwise have silently broken.

style-src is 'unsafe-inline' rather than nonce'd — a deliberately weaker, pragmatic
tradeoff, not an oversight: a dozen-plus templates use small style="..." attributes for
one-off layout tweaks, and the vendored Mermaid renderer (data-dictionary.html) injects
its own <style> elements at runtime with no nonce support at all, so a strict style-src
would require either rewriting every inline style into a CSS class or serving a different
CSP on that one page. Inline CSS also can't execute script the way inline JS can — the
practical risk 'unsafe-inline' accepts here (CSS-based UI redress / attribute-selector
data leaks) is real but far narrower than what a relaxed script-src would have accepted."""

import secrets

from fastapi import Request, Response

from openclear.config import get_settings
from openclear.web.security import is_https_deployment

def new_csp_nonce() -> str:
    return secrets.token_urlsafe(16)


def _build_csp(nonce: str, frame_ancestors: str = "'none'") -> str:
    return "; ".join(
        [
            "default-src 'self'",
            f"script-src 'self' 'nonce-{nonce}'",
            "style-src 'self' 'unsafe-inline'",
            "img-src 'self' data:",
            "font-src 'self'",
            "connect-src 'self'",
            f"frame-ancestors {frame_ancestors}",
            "form-action 'self'",
            "base-uri 'self'",
            "object-src 'none'",
        ]
    )


def apply_security_headers(request: Request, response: Response) -> None:
    # main.py's middleware sets request.state.csp_nonce before the route handler runs, so
    # templates (see login_webauthn.html etc.) and this header end up using the exact
    # same value -- the `or` fallback only matters for a response short-circuited before
    # that assignment ever ran (shouldn't happen given main.py's middleware order, but a
    # missing nonce must never silently produce a CSP with no 'nonce-' clause at all).
    nonce = getattr(request.state, "csp_nonce", None) or new_csp_nonce()
    # A route that set a stricter policy of its own (the sandboxed branding images in
    # web/routers/branding.py) keeps it; every other response gets the page policy.
    # An embedded session's pages may be framed by its online banking origin, and only
    # those (web/deps.py::get_web_context sets this); everything else, never.
    frame_ancestor = getattr(request.state, "frame_ancestor", None)
    if "content-security-policy" not in response.headers:
        response.headers["Content-Security-Policy"] = _build_csp(nonce, frame_ancestor or "'none'")
    response.headers["X-Content-Type-Options"] = "nosniff"
    # Belt-and-suspenders with frame-ancestors above — X-Frame-Options is the older
    # header every browser still honors, CSP's frame-ancestors is what modern ones prefer.
    # It can't name an origin, so an embedded page relies on frame-ancestors alone.
    if frame_ancestor is None:
        response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = (
        "camera=(), microphone=(), geolocation=(), "
        "publickey-credentials-get=(self), publickey-credentials-create=(self)"
    )
    # Signed-in pages and API responses carry account numbers, amounts, and names:
    # never let the browser (or a shared proxy) keep a copy — otherwise Back after logout,
    # or the next person at a shared workstation, can still see them. Responses that chose
    # their own caching (e.g. the public tenant logo at /ui/branding) are left alone, as is
    # /static.
    path = request.url.path
    if (path.startswith("/ui") or path.startswith("/api")) and "cache-control" not in response.headers:
        response.headers["Cache-Control"] = "no-store"
    if is_https_deployment():
        # Only meaningful (and only sent) over an actually-HTTPS deployment — see
        # is_https_deployment()'s own docstring for why this isn't unconditional.
        response.headers["Strict-Transport-Security"] = f"max-age={get_settings().hsts_max_age_seconds}; includeSubDomains"
