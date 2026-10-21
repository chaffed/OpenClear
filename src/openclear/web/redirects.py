# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed
"""Every redirect the web pages make (CODE_SCANNING_PLAN.md CS2).

- `see_other(path, **query)`: to a page of this site. The path must be local (`/ui/...`, which
  may carry a fixed query): anything with a scheme or a host, `//`, a backslash or a control
  character is refused and goes to the dashboard instead, so no route can be made into an open
  redirect, whatever it builds the path from. The keyword arguments are the query
  (`flash=`, `error=`, `next=`), encoded here; `None` leaves a parameter out.
- `to_identity_provider(url)`: the three redirects that leave the site on purpose, to a bank's
  own identity provider (OIDC sign-in, SAML sign-in and sign-out). Their URLs come from the
  provider's configuration, never from the request.

A test (tests/test_web/test_redirects.py) fails if a route builds a RedirectResponse itself."""

import logging
import re
from urllib.parse import urlencode, urlsplit

from fastapi.responses import RedirectResponse

logger = logging.getLogger(__name__)
FALLBACK = "/ui/"


def is_local(url: str | None) -> bool:
    """A path on this site, so never an open redirect: a single leading slash (`//host` is
    protocol-relative to another site), no scheme or host, and no backslash or control character
    (browsers read `/\\evil.example` as `//evil.example`, and strip tabs and newlines first)."""
    if not url or not url.startswith("/") or url.startswith("//") or "\\" in url:
        return False
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in url):
        return False
    parts = urlsplit(url)
    return not parts.scheme and not parts.netloc


_CHECKLIST = re.compile(r"^/ui/customers/[0-9a-f-]{36}/wizard$")


def checklist_return(path: str | None) -> str | None:
    """A customer's Getting Started checklist, when a form was opened from one of its steps
    (UI_PLAN.md UI-D12): saving goes back there. Only that exact shape of path is accepted."""
    return path if path and _CHECKLIST.match(path) else None


def see_other(path: str, /, **query: object) -> RedirectResponse:
    if not is_local(path):
        # Not the path itself: it can carry what someone typed (CODE_SCANNING_PLAN.md CS2).
        logger.warning("Refused a redirect that isn't a local path")
        path, query = FALLBACK, {}
    params = {key: value for key, value in query.items() if value is not None}
    if params:
        path += ("&" if "?" in path else "?") + urlencode(params)
    return RedirectResponse(path, status_code=303)


def to_identity_provider(url: str) -> RedirectResponse:
    """To the bank's own identity provider, at a URL from the connection's configuration."""
    return RedirectResponse(url, status_code=303)
