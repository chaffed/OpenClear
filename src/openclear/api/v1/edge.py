# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""How this request reached OpenClear (WAF_PLAN.md W4): for the edge check
(`scripts/edge_check.py`) and anyone setting up an edge network in front. Says only what
the request itself carried: the caller's address as OpenClear worked it out and from where,
whether the edge was trusted, which edge headers arrived, and the request id. No sign-in
needed; nothing is changed."""

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(prefix="/edge", tags=["edge"])


class EdgeEcho(BaseModel):
    client_ip: str | None
    client_ip_from: str  # "header" (the edge's client-IP header), "forwarded", or "connection"
    edge_trusted: bool
    edge_headers: list[str]
    request_id: str | None


@router.get("/echo", response_model=EdgeEcho)
def echo() -> EdgeEcho:
    """How OpenClear saw this request: the caller's address and where it came from, whether
    the connection came from a trusted edge, which edge headers arrived, and the request id."""
    from openclear.web import request_context

    context = request_context.current()
    return EdgeEcho(client_ip=context.client_ip if context else None, client_ip_from=context.ip_source if context else "connection",
                    edge_trusted=bool(context and context.edge_trusted), edge_headers=list(context.edge_hints) if context else [],
                    request_id=context.request_id if context else None)
