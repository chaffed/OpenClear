# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""OpenClear's published releases, for the host's software currency check (CONTROLS_PLAN.md
CT2; services/control_checks.py::software_currency): read from `release_feed_url` (GitHub's
releases API, or a mirror in the same shape) and kept in memory for 12 hours, so the Controls
page doesn't call out on every view. A failure is kept for an hour, so an installation that
can't reach the feed isn't slowed down by trying on each view."""

import threading
import time
from dataclasses import dataclass
from datetime import datetime

import httpx

from openclear.config import get_settings

FRESH_SECONDS = 12 * 3600
RETRY_SECONDS = 3600
TIMEOUT_SECONDS = 3.0


@dataclass(frozen=True)
class Release:
    version: tuple[int, ...]
    tag: str
    published_at: datetime


class FeedError(Exception):
    pass


_lock = threading.Lock()
_cached: tuple[float, str, list[Release] | FeedError] | None = None  # (fetched at, url, releases or the failure)


def parse_version(text: str) -> tuple[int, ...] | None:
    """`v1.19.0` or `1.19.0` as (1, 19, 0); None for anything else (a pre-release, a build)."""
    parts = text.strip().removeprefix("v").split(".")
    return tuple(int(p) for p in parts) if parts and all(p.isdigit() for p in parts) else None


def _fetch(url: str) -> list[Release]:
    try:
        response = httpx.get(url, timeout=TIMEOUT_SECONDS, follow_redirects=False,  # a fixed URL: a redirect is unexpected (ASVS 15.3.2)
                             headers={"Accept": "application/vnd.github+json", "User-Agent": "OpenClear software currency check"})
        response.raise_for_status()
        rows = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise FeedError(f"{type(exc).__name__}: {exc}"[:300]) from None
    releases = []
    for row in rows if isinstance(rows, list) else []:
        version = parse_version(str(row.get("tag_name") or ""))
        if version is None or row.get("draft") or row.get("prerelease") or not row.get("published_at"):
            continue
        releases.append(Release(version, row["tag_name"], datetime.fromisoformat(row["published_at"].replace("Z", "+00:00"))))
    if not releases:
        raise FeedError("The feed lists no releases.")
    return sorted(releases, key=lambda r: r.version)


def releases() -> list[Release] | None:
    """The published releases, oldest first; None when no feed is configured. Raises FeedError
    when the feed can't be read (remembered for an hour)."""
    global _cached
    url = get_settings().release_feed_url.strip()
    if not url:
        return None
    with _lock:
        now = time.monotonic()
        if _cached and _cached[1] == url:
            fetched, _url, value = _cached
            if now - fetched < (RETRY_SECONDS if isinstance(value, FeedError) else FRESH_SECONDS):
                if isinstance(value, FeedError):
                    raise value
                return value
        try:
            value = _fetch(url)
        except FeedError as exc:
            _cached = (now, url, exc)
            raise
        _cached = (now, url, value)
        return value


def forget() -> None:
    """Drop what's cached (tests, and after changing the feed)."""
    global _cached
    with _lock:
        _cached = None
