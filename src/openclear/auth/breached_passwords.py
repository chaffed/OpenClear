# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed
"""Is this password known to be breached? (OWASP_PLAN.md OW-D15; ASVS 5.0 6.2.4, 6.2.12.)

Three sources, in order, the first "yes" winning:
1. The bundled list (data/breached-passwords.bin, from scripts/build_breached_list.py): the
   first 8 bytes of the SHA-1 of the 100,000 most common passwords, sorted. Always checked.
2. Settings.breached_password_file: a bigger list in Have I Been Pwned's download format,
   sorted "SHA1:count" lines (any line ending), binary-searched by seeking.
3. Settings.breached_password_hibp: HIBP's range API. Only the first five hex characters of
   the hash are sent; the answer lists every suffix with that prefix. If HIBP can't be
   reached, the answer is "not known" and a warning is logged: an outage must not stop
   anyone from setting a password."""

import bisect
import hashlib
import logging
import os
from functools import lru_cache
from pathlib import Path

from openclear.config import get_settings

logger = logging.getLogger(__name__)

_BUNDLED = Path(__file__).resolve().parent / "data" / "breached-passwords.bin"
_PREFIX_BYTES = 8
_HIBP_RANGE_URL = "https://api.pwnedpasswords.com/range/"


class _Prefixes:
    """A sorted bytes blob of fixed-size records, searchable with bisect without copying it."""

    def __init__(self, blob: bytes, size: int) -> None:
        self._blob, self._size = blob, size

    def __len__(self) -> int:
        return len(self._blob) // self._size

    def __getitem__(self, i: int) -> bytes:
        return self._blob[i * self._size:(i + 1) * self._size]


@lru_cache(maxsize=1)
def _bundled() -> _Prefixes:
    return _Prefixes(_BUNDLED.read_bytes(), _PREFIX_BYTES)


def _in_bundled(digest: bytes) -> bool:
    prefixes = _bundled()
    i = bisect.bisect_left(prefixes, digest[:_PREFIX_BYTES])
    return i < len(prefixes) and prefixes[i] == digest[:_PREFIX_BYTES]


def _in_file(path: str, sha1_hex: str) -> bool:
    """Binary search over a sorted text file of "HEX:count" lines, by byte offset: seek to the
    middle, skip to the next line start, compare."""
    target = sha1_hex.encode("ascii")
    with open(path, "rb") as f:
        lo, hi = 0, os.fstat(f.fileno()).st_size
        while lo < hi:
            mid = (lo + hi) // 2
            f.seek(mid)
            if mid:
                f.readline()  # finish the line we landed in
            start = f.tell()
            line = f.readline()
            if not line or start >= hi:
                hi = mid
                continue
            key = line.split(b":", 1)[0].strip().upper()
            if key == target:
                return True
            if key < target:
                lo = f.tell()
            else:
                hi = mid
        # The bisection can skip the very first line (it never seeks to 0 then reads it).
        f.seek(0)
        return f.readline().split(b":", 1)[0].strip().upper() == target


def _in_hibp(sha1_hex: str, timeout: float) -> bool | None:
    from openclear.auth.outbound_http import public_only_client

    prefix, suffix = sha1_hex[:5], sha1_hex[5:]
    try:
        with public_only_client(timeout=timeout) as client:
            response = client.get(_HIBP_RANGE_URL + prefix, headers={"Add-Padding": "true"})
            response.raise_for_status()
    except Exception as exc:  # any network or HTTP failure: fall back to the lists
        logger.warning("breached-password check: HIBP unavailable (%s); the bundled list decided", type(exc).__name__)
        return None
    for line in response.text.splitlines():
        found, _, count = line.partition(":")
        if found.strip().upper() == suffix and count.strip() != "0":  # padding entries have count 0
            return True
    return False


def is_breached(password: str) -> bool:
    # SHA-1 only because the lists are indexed by it (HIBP's format); it protects nothing here.
    digest = hashlib.sha1(password.encode("utf-8"), usedforsecurity=False).digest()
    if _in_bundled(digest):
        return True
    settings = get_settings()
    sha1_hex = digest.hex().upper()
    if settings.breached_password_file and _in_file(settings.breached_password_file, sha1_hex):
        return True
    if settings.breached_password_hibp:
        return bool(_in_hibp(sha1_hex, settings.breached_password_hibp_timeout_seconds))
    return False
