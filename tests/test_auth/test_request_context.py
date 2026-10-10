# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Each request's context behind an edge network (MASTER_PLAN.md M1; WAF_PLAN.md W1–W3;
web/request_context.py): the caller's IP, request ids, the edge secret, rate limits per
person, the source IP in the action log, the edge control, and caching."""

import logging

import pytest
from sqlalchemy import select
from starlette.requests import Request

from openclear.config import get_settings
from openclear.domain.audit_log_entry import AuditLogEntry
from openclear.domain.security_event import SecurityEvent
from openclear.web import request_context
from tests.conftest import login_headers

CLOUDFLARE = "173.245.48.0/20"


def _request(peer: str, headers: dict[str, str] | None = None, path: str = "/ui/") -> Request:
    scope = {"type": "http", "method": "GET", "path": path, "query_string": b"", "client": (peer, 1234),
             "headers": [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]}
    return Request(scope)


@pytest.fixture
def settings(monkeypatch):
    settings = get_settings()
    for name, value in (("client_ip_header", None), ("trusted_proxy_cidrs", ""), ("trusted_proxy_count", 0),
                        ("edge_secret", None), ("edge_secret_previous", None), ("edge_attestation", None)):
        monkeypatch.setattr(settings, name, value)
    return settings


def test_the_callers_ip(settings, monkeypatch):
    spoof = {"cf-connecting-ip": "198.51.100.9", "x-forwarded-for": "198.51.100.9"}
    # Nothing configured: the connection, whatever headers say.
    assert request_context.resolve_client_ip(_request("203.0.113.5", spoof)) == ("203.0.113.5", "connection", False)

    # An edge's header, trusted only from the edge's ranges.
    monkeypatch.setattr(settings, "client_ip_header", "CF-Connecting-IP")
    monkeypatch.setattr(settings, "trusted_proxy_cidrs", f"{CLOUDFLARE}, 2400:cb00::/32")
    assert request_context.resolve_client_ip(_request("173.245.48.10", spoof)) == ("198.51.100.9", "header", True)
    assert request_context.resolve_client_ip(_request("203.0.113.5", spoof))[0] == "203.0.113.5"  # not from the edge
    assert request_context.resolve_client_ip(_request("173.245.48.10", {"cf-connecting-ip": "not-an-ip"}))[0] == "173.245.48.10"

    # A plain reverse proxy: hops from the right of X-Forwarded-For.
    monkeypatch.setattr(settings, "client_ip_header", None)
    monkeypatch.setattr(settings, "trusted_proxy_count", 1)
    assert request_context.resolve_client_ip(_request("10.0.0.2", {"x-forwarded-for": "1.1.1.1, 198.51.100.7"}))[0] == "198.51.100.7"


def test_request_ids_and_the_edge_secret(client, settings, monkeypatch):
    first = client.get("/health")
    assert len(first.headers["x-request-id"]) == 16 and first.headers["x-request-id"] != client.get("/health").headers["x-request-id"]
    # A trusted edge's own id becomes the request id.
    monkeypatch.setattr(settings, "trusted_proxy_count", 1)
    assert client.get("/health", headers={"cf-ray": "8c2f1a-IAD", "x-forwarded-for": "198.51.100.7"}).headers["x-request-id"] == "8c2f1a-IAD"

    monkeypatch.setattr(settings, "edge_secret", "s3cret-edge")
    assert client.get("/health").status_code == 200  # load balancers' checks still pass
    assert client.get("/ui/login").status_code == 403
    assert client.get("/ui/login", headers={"X-OpenClear-Edge-Secret": "s3cret-edge"}).status_code == 200
    monkeypatch.setattr(settings, "edge_secret", "new-edge-secret")
    monkeypatch.setattr(settings, "edge_secret_previous", "s3cret-edge")  # rotating
    assert client.get("/ui/login", headers={"X-OpenClear-Edge-Secret": "s3cret-edge"}).status_code == 200
    assert client.get("/ui/login", headers={"X-OpenClear-Edge-Secret": "wrong"}).status_code == 403


def test_signed_in_callers_are_limited_as_themselves(client, db_session, tenant_factory, settings, monkeypatch):
    tenant, _account, users = tenant_factory.make(slug="ctx-limits")
    headers = login_headers(client, tenant.slug, users["admin"].email)
    monkeypatch.setattr(settings, "rate_limit_per_minute", 3)
    for _ in range(5):  # one shared address, but a signed-in caller has a bucket of their own
        assert client.get("/api/v1/accounts", headers=headers).status_code == 200
    for _ in range(3):
        client.get("/health")
    assert client.get("/health").status_code == 429  # anonymous: by address
    [event] = db_session.execute(select(SecurityEvent).where(SecurityEvent.name == "rate_limit.exceeded")).scalars().all()
    assert event.details["keyed_by"] == "ip"


def test_the_action_log_records_where_and_which_request(client, db_session, tenant_factory, settings, monkeypatch):
    tenant, account, users = tenant_factory.make(slug="ctx-audit")
    monkeypatch.setattr(settings, "trusted_proxy_count", 1)
    headers = {**login_headers(client, tenant.slug, users["preparer"].email), "x-forwarded-for": "198.51.100.42"}
    response = client.post("/api/v1/paid-items", headers=headers, json={
        "account_id": str(account.id), "check_number": "CTX-1", "presented_amount": "5.00", "presented_date": "2026-03-02"})
    assert response.status_code == 201
    entry = db_session.execute(select(AuditLogEntry).where(AuditLogEntry.tenant_id == tenant.id,
                                                           AuditLogEntry.action == "paid_item.create")).scalar_one()
    assert entry.metadata_json["source_ip"] == "198.51.100.42" and entry.metadata_json["request_id"] == response.headers["x-request-id"]
    from openclear.services import audit_log_service

    assert audit_log_service.verify_chain(db_session, tenant.id).valid  # still signed and chained


def test_the_edge_control(settings, monkeypatch):
    from openclear.services import controls_service

    def edge(peer, headers):
        token = request_context.set_current(request_context.build(_request(peer, headers)))
        try:
            return controls_service._edge()
        finally:
            request_context.reset(token)

    assert edge("203.0.113.5", {}).status == "PASS"
    assert edge("173.245.48.10", {"cf-ray": "x", "cf-connecting-ip": "198.51.100.9"}).status == "FAIL"  # in front, not set up
    monkeypatch.setattr(settings, "client_ip_header", "CF-Connecting-IP")
    monkeypatch.setattr(settings, "trusted_proxy_cidrs", CLOUDFLARE)
    assert edge("173.245.48.10", {"cf-ray": "x", "cf-connecting-ip": "198.51.100.9"}).status == "WARN"  # no lockdown
    assert edge("203.0.113.5", {"cf-ray": "x", "cf-connecting-ip": "198.51.100.9"}).status == "WARN"  # around the edge
    monkeypatch.setattr(settings, "edge_attestation", "Cloudflare Tunnel; no inbound ports")
    passed = edge("173.245.48.10", {"cf-ray": "x", "cf-connecting-ip": "198.51.100.9"})
    assert passed.status == "PASS" and "Tunnel" in passed.detail


def test_static_caching_and_json_413(client, settings, monkeypatch):
    from openclear.web.templates import _asset_version

    version = _asset_version("css/app.css")
    assert client.get(f"/static/css/app.css?v={version}").headers["cache-control"] == "public, max-age=31536000, immutable"
    assert client.get("/static/css/app.css?v=1").headers["cache-control"] == "public, max-age=300"
    assert client.get("/static/css/app.css").headers["cache-control"] == "public, max-age=300"
    monkeypatch.setattr(settings, "max_request_body_bytes", 10)
    refused = client.post("/api/v1/oauth/token", content=b"x" * 50, headers={"content-type": "application/x-www-form-urlencoded"})
    assert refused.status_code == 413 and "too large" in refused.json()["detail"]


def test_json_logs_carry_the_request(client, tenant_factory, settings, monkeypatch, capsys):
    import json

    from openclear import logging_config

    tenant, _account, users = tenant_factory.make(slug="ctx-logs")
    monkeypatch.setattr(settings, "log_format", "json")
    root = logging.getLogger()
    saved_handlers, saved_level = list(root.handlers), root.level
    try:
        logging_config.configure(settings)
        headers = login_headers(client, tenant.slug, users["admin"].email)
        client.get("/api/v1/accounts?secret=do-not-log", headers=headers)
        lines = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.startswith("{")]
    finally:
        for handler in list(root.handlers):
            root.removeHandler(handler)
        for handler in saved_handlers:
            root.addHandler(handler)
        root.setLevel(saved_level)
        monkeypatch.setattr(settings, "log_format", "text")
        logging_config.configure(settings)
    access = [line for line in lines if line["logger"] == "openclear.access" and line["http"]["path"] == "/api/v1/accounts"]
    assert access and access[-1]["http"]["status"] == 200 and access[-1]["bank"] == "ctx-logs"
    assert access[-1]["request"]["id"] and access[-1]["client"]["ip"]
    assert not any("do-not-log" in json.dumps(line) for line in lines), [l["logger"] for l in lines if "do-not-log" in json.dumps(l)]
