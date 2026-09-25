# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

from fastapi import Request

from openclear.config import get_settings


def get_client_ip(request: Request) -> str | None:
    """The real caller's IP -- the direct TCP peer by default. That's safe with nothing
    configured: a client can send any header it likes, but can't fake its own TCP source
    address. Behind a reverse proxy/WAF (OPENCLEAR_TRUSTED_PROXY_COUNT > 0), instead trusts
    exactly that many entries from the *right* end of X-Forwarded-For -- the hop your own
    proxy chain appended, never the client-supplied left end, which is trivially
    spoofable. Leaving this at 0 (the default) never reads the header at all, so a
    missing or misconfigured proxy can't silently start trusting a forged header."""
    trusted_hops = get_settings().trusted_proxy_count
    if trusted_hops > 0:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            hops = [hop.strip() for hop in forwarded.split(",") if hop.strip()]
            if len(hops) >= trusted_hops:
                return hops[-trusted_hops]
    return request.client.host if request.client else None
