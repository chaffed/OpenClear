# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed
"""Every redirect the web pages make (CODE_SCANNING_PLAN.md CS2).

- `see_other(url)`: to a page of this site. The URL must be a local path (`/ui/...`, with a
  query): anything with a scheme or a host, `//`, a backslash or a control character is refused
  and goes to the dashboard instead, so no route can be made into an open redirect, whatever
  it builds the path from.
- `to_identity_provider(url)`: the three redirects that leave the site on purpose, to a bank's
  own identity provider (OIDC sign-in, SAML sign-in and sign-out). Their URLs come from the
  provider's configuration, never from the request.

A test (tests/test_web/test_redirects.py) fails if a route builds a RedirectResponse itself."""

import logging
from urllib.parse import urlsplit

from fastapi.responses import RedirectResponse

logger = logging.getLogger(__name__)
FALLBACK = "/ui/"


def is_local(url: str) -> bool:
    """A path on this site: starts with one slash, no scheme or host, no backslash or control
    characters (browsers read `/\\evil.example` as `//evil.example`)."""
    if not url or not url.startswith("/") or url.startswith("//") or "\\" in url:
        return False
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in url):
        return False
    parts = urlsplit(url)
    return not parts.scheme and not parts.netloc


def see_other(url: str, status_code: int = 303, headers: dict | None = None) -> RedirectResponse:
    if not is_local(url):
        # Not the URL itself: it can carry what someone typed (CODE_SCANNING_PLAN.md CS2).
        logger.warning("Refused a redirect that isn't a local path")
        url = FALLBACK
    return RedirectResponse(url, status_code=status_code, headers=headers)


def to_identity_provider(url: str, status_code: int = 303) -> RedirectResponse:
    """To the bank's own identity provider, at a URL from the connection's configuration."""
    return RedirectResponse(url, status_code=status_code)
