# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Each request's context, worked out once (MASTER_PLAN.md M1; WAF_PLAN.md W1–W2,
SIEM_PLAN.md S1–S2): the caller's IP, the request id, and later who is making it. Rate
limits, the action log, JSON logs, security events and error pages all read it from here,
so they can never disagree about who or where a request came from.

**The caller's IP**, in order:
1. With OPENCLEAR_CLIENT_IP_HEADER (e.g. `CF-Connecting-IP`, `True-Client-IP`): that header,
   but only when the connection itself comes from OPENCLEAR_TRUSTED_PROXY_CIDRS (the edge's
   address ranges). A header from anywhere else is ignored: it may be forged.
2. With OPENCLEAR_TRUSTED_PROXY_COUNT=N: the Nth address from the right of
   X-Forwarded-For (for a plain reverse proxy with a fixed number of hops).
3. Otherwise the connection's own address.

**The request id** is the edge's own id (`CF-Ray`, Akamai's `X-Akamai-Request-ID`, or
`X-Request-ID`) when the edge is trusted, or a new one; the edge's id is also kept
separately. It's returned as `X-Request-ID` and shown on error pages."""

import ipaddress
import secrets
from contextvars import ContextVar
from dataclasses import dataclass, field
from functools import lru_cache

from starlette.requests import Request

from openclear.config import get_settings

EDGE_ID_HEADERS = ("cf-ray", "x-akamai-request-id", "akamai-request-id", "x-request-id")
# Headers that show an edge network is in front, whether or not it's configured.
EDGE_HINT_HEADERS = ("cf-ray", "cf-connecting-ip", "true-client-ip", "akamai-origin-hop", "x-akamai-request-id",
                     "x-akamai-edgescape", "cdn-loop", "fly-client-ip")


@dataclass
class RequestContext:
    request_id: str
    client_ip: str | None
    ip_source: str  # "header", "forwarded", "connection"
    peer_ip: str | None
    edge_id: str | None = None
    edge_trusted: bool = False
    edge_hints: tuple[str, ...] = ()
    channel: str = "web"  # "api" for /api and /scim, else "web"
    user_agent: str | None = None
    # Filled in once the request is authenticated (auth/deps.py, web/deps.py).
    tenant_id: object = None
    tenant_slug: str | None = None
    user_id: object = None
    user_email: str | None = None
    api_client_id: object = None
    extra: dict = field(default_factory=dict)


_current: ContextVar[RequestContext | None] = ContextVar("openclear_request_context", default=None)


def current() -> RequestContext | None:
    return _current.get()


def set_current(context: RequestContext | None):
    return _current.set(context)


def reset(token) -> None:
    _current.reset(token)


@lru_cache(maxsize=8)
def _networks(spec: str) -> tuple:
    networks = []
    for part in (spec or "").replace("\n", ",").split(","):
        part = part.strip()
        if part:
            networks.append(ipaddress.ip_network(part, strict=False))
    return tuple(networks)


def trusted_networks() -> tuple:
    return _networks(get_settings().trusted_proxy_cidrs or "")


def _in(ip: str | None, networks: tuple) -> bool:
    if not ip or not networks:
        return False
    try:
        address = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return any(address in network for network in networks)


def _valid_ip(value: str | None) -> str | None:
    value = (value or "").strip()
    try:
        return str(ipaddress.ip_address(value)) if value else None
    except ValueError:
        return None


def resolve_client_ip(request: Request) -> tuple[str | None, str, bool]:
    """(client IP, where it came from, whether the edge is trusted for this request)."""
    settings = get_settings()
    peer = request.client.host if request.client else None
    header = (settings.client_ip_header or "").strip().lower()
    if header:
        if _in(peer, trusted_networks()):
            value = _valid_ip(request.headers.get(header, "").split(",")[0])
            if value:
                return value, "header", True
        return peer, "connection", False
    hops = settings.trusted_proxy_count
    if hops > 0:
        forwarded = [hop.strip() for hop in request.headers.get("x-forwarded-for", "").split(",") if hop.strip()]
        if len(forwarded) >= hops:
            return forwarded[-hops], "forwarded", True
    return peer, "connection", False


def build(request: Request) -> RequestContext:
    client_ip, source, trusted = resolve_client_ip(request)
    edge_id = None
    if trusted:
        for name in EDGE_ID_HEADERS:
            value = request.headers.get(name, "").strip()
            if value:
                edge_id = value[:100]
                break
    path = request.url.path
    return RequestContext(
        request_id=(edge_id if edge_id and len(edge_id) <= 64 else None) or secrets.token_hex(8),
        client_ip=client_ip, ip_source=source, peer_ip=request.client.host if request.client else None,
        edge_id=edge_id, edge_trusted=trusted,
        edge_hints=tuple(name for name in EDGE_HINT_HEADERS if name in request.headers),
        channel="api" if path.startswith(("/api/", "/scim/")) else "web",
        user_agent=(request.headers.get("user-agent") or "")[:300] or None,
    )


def note_identity(*, tenant_id=None, tenant_slug=None, user_id=None, user_email=None, api_client_id=None) -> None:
    """Records who the request is for, once it's authenticated."""
    context = current()
    if context is None:
        return
    if tenant_id is not None:
        context.tenant_id, context.tenant_slug = tenant_id, tenant_slug or context.tenant_slug
    if user_id is not None:
        context.user_id = user_id
    if user_email:
        context.user_email = user_email
    if api_client_id is not None:
        context.api_client_id = api_client_id


def edge_secret_ok(request: Request) -> bool:
    """With OPENCLEAR_EDGE_SECRET set, only requests carrying it (in
    OPENCLEAR_EDGE_SECRET_HEADER) are let in, apart from /health. Two values may be set
    while one is being rotated."""
    import hmac

    settings = get_settings()
    secrets_ = [s for s in (settings.edge_secret, settings.edge_secret_previous) if s]
    if not secrets_ or request.url.path == "/health":
        return True
    given = request.headers.get(settings.edge_secret_header, "")
    return any(hmac.compare_digest(given.encode(), secret.encode()) for secret in secrets_)
