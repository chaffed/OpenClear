# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""A small in-memory, per-process sliding-window rate limiter -- hand-rolled rather than
a new dependency, since a distributed/Redis-backed limiter isn't needed for the one
deployment model this repo documents: scripts/launcher.py runs a single uvicorn process
with no workers=N, so per-process in-memory counters are the correct fit here, not a
shortcut. (A future multi-worker production launch would give each worker its own
counters, multiplying the effective limit by worker count -- a deployment-time
consideration, not something this module can fix on its own.)

Used two ways: main.py's global middleware applies one generous default limit to every
request, and individual routes (see markdown_preview.py) can layer a stricter,
route-specific limit on top via the rate_limit() dependency factory below.

Memory is bounded two ways, both handled inline rather than needing a background thread:
an active key's own deque never holds more than `limit` timestamps (expired ones are
trimmed on every call for that key), and a periodic full-dict sweep (RateLimiter._sweep,
triggered every _SWEEP_EVERY_N_CALLS calls) drops keys nobody has hit in a while -- e.g. a
one-off visitor's IP, which would otherwise never get revisited and so never get its own
per-key trim to run again.
"""

import time
from collections import deque
from threading import Lock

from fastapi import HTTPException, Request, status

from openclear.config import get_settings
from openclear.web.client_ip import get_client_ip


class RateLimiter:
    # A generous upper bound above any bucket's own window_seconds (every bucket in this
    # app uses <= 60s) -- used only by the periodic sweep below to decide when an
    # inactive key is safe to garbage-collect, never to enforce an actual limit.
    _STALE_AFTER_SECONDS = 300.0
    _SWEEP_EVERY_N_CALLS = 500

    def __init__(self) -> None:
        self._hits: dict[tuple[str, str], deque[float]] = {}
        self._lock = Lock()
        self._calls_since_sweep = 0

    def allow(self, key: str, bucket: str, *, limit: int, window_seconds: float) -> bool:
        """True if another hit for (key, bucket) is allowed right now, recording it if
        so. Each call trims its own key's expired entries first, which bounds an
        individual active key's memory to at most `limit` timestamps -- but that alone
        doesn't bound total memory, since a key nobody ever asks about again (a one-off
        visitor) would otherwise sit in the dict forever with its last, now-expired hit.
        A periodic full-dict sweep (below) closes that gap without needing a background
        thread."""
        now = time.monotonic()
        dict_key = (key, bucket)
        with self._lock:
            hits = self._hits.get(dict_key, deque())
            while hits and now - hits[0] > window_seconds:
                hits.popleft()
            allowed = len(hits) < limit
            if allowed:
                hits.append(now)
            if hits:
                self._hits[dict_key] = hits
            else:
                self._hits.pop(dict_key, None)

            self._calls_since_sweep += 1
            if self._calls_since_sweep >= self._SWEEP_EVERY_N_CALLS:
                self._calls_since_sweep = 0
                self._sweep(now)
            return allowed

    def _sweep(self, now: float) -> None:
        """Garbage-collects any key whose newest hit is old enough that every bucket's
        real window has certainly elapsed -- called with self._lock already held."""
        stale_keys = [k for k, hits in self._hits.items() if not hits or now - hits[-1] > self._STALE_AFTER_SECONDS]
        for stale_key in stale_keys:
            del self._hits[stale_key]


# One process-wide instance, shared by the global middleware and every per-route
# dependency -- each bucket name is tracked independently, but all under the same lock.
limiter = RateLimiter()


def rate_limit(bucket: str, *, limit_setting: str, window_seconds: float = 60.0):
    """FastAPI dependency factory for a stricter, per-route limit on top of the global
    default -- same "factory returning an inner callable, wrapped in Depends() by the
    caller" idiom as web/deps.py::require_web_permission. limit_setting is a Settings
    attribute name (not a bare int) so the limit is read live off get_settings() on every
    request, same as every other request-time settings read in this app."""

    def _check(request: Request) -> None:
        limit = getattr(get_settings(), limit_setting)
        client_ip = get_client_ip(request) or "unknown"
        if not limiter.allow(client_ip, bucket, limit=limit, window_seconds=window_seconds):
            raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many requests -- please slow down.")

    return _check
