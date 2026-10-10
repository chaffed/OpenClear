# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

from fastapi import Request


def get_client_ip(request: Request) -> str | None:
    """The real caller's IP, as the request context worked it out (web/request_context.py):
    a trusted edge's client-IP header, the right end of X-Forwarded-For behind a configured
    number of proxies, or the TCP connection's own address. With nothing configured, only
    the connection's address is used: a client can send any header it likes, but can't fake
    its own TCP source address."""
    from openclear.web import request_context

    context = request_context.current()
    if context is not None:
        return context.client_ip
    return request_context.resolve_client_ip(request)[0]
