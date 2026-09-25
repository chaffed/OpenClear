# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import time

from openclear.web.rate_limit import RateLimiter


def test_allows_up_to_the_limit_then_blocks():
    limiter = RateLimiter()

    for _ in range(3):
        assert limiter.allow("1.2.3.4", "test", limit=3, window_seconds=60) is True
    assert limiter.allow("1.2.3.4", "test", limit=3, window_seconds=60) is False


def test_buckets_are_independent_per_client_key():
    limiter = RateLimiter()
    for _ in range(3):
        limiter.allow("1.2.3.4", "test", limit=3, window_seconds=60)

    # A different IP has its own, untouched counter -- one abusive client can't exhaust
    # another's allowance.
    assert limiter.allow("5.6.7.8", "test", limit=3, window_seconds=60) is True


def test_buckets_are_independent_per_bucket_name():
    limiter = RateLimiter()
    for _ in range(3):
        limiter.allow("1.2.3.4", "global", limit=3, window_seconds=60)

    # Same IP, a different named bucket -- e.g. the stricter markdown-preview limit is
    # tracked separately from the global one, not sharing its counter.
    assert limiter.allow("1.2.3.4", "markdown_preview", limit=3, window_seconds=60) is True


def test_old_hits_age_out_of_the_window():
    limiter = RateLimiter()
    assert limiter.allow("1.2.3.4", "test", limit=1, window_seconds=0.05) is True
    assert limiter.allow("1.2.3.4", "test", limit=1, window_seconds=0.05) is False

    time.sleep(0.1)

    assert limiter.allow("1.2.3.4", "test", limit=1, window_seconds=0.05) is True


def test_an_active_keys_deque_never_exceeds_the_limit():
    limiter = RateLimiter()
    for _ in range(5):
        limiter.allow("1.2.3.4", "test", limit=2, window_seconds=0.01)
        time.sleep(0.02)  # each call's own hit has aged out by the time the next one runs

    # Every call trimmed its expired predecessor before appending its own -- the deque
    # never accumulates more than `limit` timestamps regardless of how many calls happen.
    assert len(limiter._hits[("1.2.3.4", "test")]) <= 2


def test_periodic_sweep_evicts_a_key_nobody_asked_about_again():
    limiter = RateLimiter()
    limiter._STALE_AFTER_SECONDS = 0.01
    limiter._SWEEP_EVERY_N_CALLS = 1
    limiter.allow("1.2.3.4", "test", limit=100, window_seconds=60)
    assert ("1.2.3.4", "test") in limiter._hits

    time.sleep(0.05)
    # A completely unrelated key's call is what triggers the sweep (since
    # _SWEEP_EVERY_N_CALLS=1) -- the stale "1.2.3.4" entry must be garbage-collected even
    # though nothing ever checked it again directly, or a one-off visitor's entry would
    # sit in memory forever over a long-running process.
    limiter.allow("9.9.9.9", "other", limit=100, window_seconds=60)

    assert ("1.2.3.4", "test") not in limiter._hits
