# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Sliding-window rate limiting, counted per process (the default) or shared between
every instance through Redis (`rate_limit_backend = "redis"`; SCALE_PLAN.md S6).

Per process suits a single uvicorn process, which is what scripts/launcher.py, the Docker
image and the demo run. With several processes or instances each keeps its own counters,
so the effective limit is `rate_limit_per_minute` times their number; the Redis backend
counts once for all of them. Either way, a proxy or WAF in front (README, "Reverse proxy /
WAF deployment") is the first line of defense; this is the safety net behind it.

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

import logging
import time
import uuid
from collections import deque
from functools import lru_cache
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

logger = logging.getLogger(__name__)

# Trim, check and record in one atomic step, with Redis's own clock so every instance
# agrees on the time. KEYS[1] is the (client, bucket) key; ARGV is limit, window in ms,
# and a unique member for this hit.
_SLIDING_WINDOW = """
local now = redis.call('TIME')
local now_ms = tonumber(now[1]) * 1000 + math.floor(tonumber(now[2]) / 1000)
local window_ms = tonumber(ARGV[2])
redis.call('ZREMRANGEBYSCORE', KEYS[1], 0, now_ms - window_ms)
if redis.call('ZCARD', KEYS[1]) < tonumber(ARGV[1]) then
  redis.call('ZADD', KEYS[1], now_ms, ARGV[3])
  redis.call('PEXPIRE', KEYS[1], window_ms)
  return 1
end
return 0
"""


class RedisRateLimiter:
    """The same sliding window, kept in Redis so every instance shares it. Fails open: if
    Redis can't be reached the hit is allowed and the error logged (rate-limited to one
    log line a minute), so a Redis outage doesn't take the app down with it."""

    _KEY_PREFIX = "openclear:ratelimit:"

    def __init__(self, client) -> None:
        self._client = client
        self._script = client.register_script(_SLIDING_WINDOW)
        self._last_error_logged = 0.0

    def allow(self, key: str, bucket: str, *, limit: int, window_seconds: float) -> bool:
        try:
            return bool(self._script(
                keys=[f"{self._KEY_PREFIX}{bucket}:{key}"], args=[limit, int(window_seconds * 1000), uuid.uuid4().hex]
            ))
        except Exception:  # noqa: BLE001 -- any Redis failure: allow, and say so
            if time.monotonic() - self._last_error_logged > 60:
                self._last_error_logged = time.monotonic()
                logger.exception("Rate limiting is off: Redis at rate_limit_redis_url can't be reached")
            return True


@lru_cache(maxsize=2)
def _redis_limiter(url: str) -> RedisRateLimiter:
    import redis

    return RedisRateLimiter(redis.Redis.from_url(url, socket_timeout=0.5, socket_connect_timeout=0.5))


def allow(key: str, bucket: str, *, limit: int, window_seconds: float) -> bool:
    """One hit for (key, bucket), against whichever backend is configured."""
    settings = get_settings()
    if settings.rate_limit_backend == "redis":
        return _redis_limiter(settings.rate_limit_redis_url).allow(key, bucket, limit=limit, window_seconds=window_seconds)
    return limiter.allow(key, bucket, limit=limit, window_seconds=window_seconds)


def rate_limit(bucket: str, *, limit_setting: str, window_seconds: float = 60.0):
    """FastAPI dependency factory for a stricter, per-route limit on top of the global
    default -- same "factory returning an inner callable, wrapped in Depends() by the
    caller" idiom as web/deps.py::require_web_permission. limit_setting is a Settings
    attribute name (not a bare int) so the limit is read live off get_settings() on every
    request, same as every other request-time settings read in this app."""

    def _check(request: Request) -> None:
        limit = getattr(get_settings(), limit_setting)
        client_ip = get_client_ip(request) or "unknown"
        if not allow(client_ip, bucket, limit=limit, window_seconds=window_seconds):
            raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many requests -- please slow down.")

    return _check


def principal_key(request: Request) -> str | None:
    """Who a request is from, for the global limit, when it carries a valid access token
    (an Authorization bearer, or the web session cookie): "user:<id>", which for an API
    client is its own service identity. None otherwise, and the limit falls back to the IP
    address. The signature is checked, so a made-up token can't pick its own bucket; an
    expired or invalid one counts by IP."""
    import jwt

    from openclear.auth.security import decode_token
    from openclear.web.security import ACCESS_COOKIE_NAME

    header = request.headers.get("authorization", "")
    token = header[7:].strip() if header.lower().startswith("bearer ") else request.cookies.get(ACCESS_COOKIE_NAME)
    if not token or token.count(".") != 2:
        return None
    try:
        claims = decode_token(token)
    except jwt.PyJWTError:
        return None
    if claims.get("type") != "access" or not claims.get("sub"):
        return None
    return f"user:{claims['sub']}"
