# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""The edge check (WAF_PLAN.md W4; src/openclear/edge_check.py) against a running OpenClear
behind a stub edge (tests/stub_edge.py) that behaves like Cloudflare or Akamai: a sound setup
passes, and each mistake real edge setups make is caught."""

import threading
import time

import pytest

from openclear import edge_check
from openclear.config import get_settings
from tests.stub_edge import StubEdge

SECRET = "edge-secret-for-tests-0123456789"


@pytest.fixture
def engine(engine, tmp_path):
    """A real server answers requests on several threads; give it a pooled SQLite file, as
    tests/test_sdk/test_typescript_sdk.py does."""
    if engine.dialect.name != "sqlite":
        yield engine
        return
    from sqlalchemy import create_engine, event

    from openclear.db.base import Base
    from tests.conftest import _seed_payment_networks

    pooled = create_engine(f"sqlite:///{tmp_path / 'edge.db'}", connect_args={"check_same_thread": False, "timeout": 30})

    @event.listens_for(pooled, "connect")
    def _pragmas(dbapi_connection, _):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")
        dbapi_connection.execute("PRAGMA journal_mode=WAL")

    Base.metadata.create_all(pooled)
    _seed_payment_networks(pooled)
    yield pooled
    pooled.dispose()


@pytest.fixture
def origin(app, monkeypatch, engine):
    import uvicorn

    settings = get_settings()
    for name, value in {"client_ip_header": "CF-Connecting-IP", "trusted_proxy_cidrs": "127.0.0.1/32", "edge_secret": SECRET,
                        "edge_secret_header": "X-OpenClear-Edge-Secret"}.items():
        monkeypatch.setattr(settings, name, value)
    # The edge's headers decide the caller's address, not uvicorn's own proxy handling.
    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning", lifespan="off", proxy_headers=False)
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 15
    while not server.started:
        assert time.monotonic() < deadline, "server didn't start"
        time.sleep(0.05)
    yield f"http://127.0.0.1:{server.servers[0].sockets[0].getsockname()[1]}"
    server.should_exit = True
    thread.join(timeout=10)


def _edge(origin, **kwargs):
    return StubEdge(origin, secret=SECRET, secret_header="X-OpenClear-Edge-Secret", **kwargs)


def _by_key(checks):
    return {c.key: c for c in checks}


@pytest.mark.parametrize("kind", ["cloudflare", "akamai"])
def test_a_sound_setup_passes(origin, monkeypatch, kind):
    if kind == "akamai":
        monkeypatch.setattr(get_settings(), "client_ip_header", "True-Client-IP")
    edge = _edge(origin, kind=kind, client_ip="198.51.100.23")
    try:
        checks = edge_check.run(edge.url, origin_url=origin, expect_ip="198.51.100.23", burst=8)
    finally:
        edge.close()
    results = _by_key(checks)
    assert not edge_check.failed(checks), edge_check.report(checks)
    assert results["client_ip"].status == "PASS" and "198.51.100.23" in results["client_ip"].detail
    assert results["origin_lockdown"].status == "PASS"  # direct requests lack the edge secret
    assert results["not_blocked"].status == "PASS" and len(results["not_blocked"].evidence["probes"]) == 11
    assert results["static_cached"].status == "PASS" and results["compression"].status == "PASS"
    assert results["reachable"].detail.endswith(("Cloudflare.", "Akamai."))


@pytest.mark.parametrize("fault,key,needle", [
    ("no_client_ip", "client_ip", "OPENCLEAR_CLIENT_IP_HEADER"),
    ("inject_scripts", "injection", "rocket-loader"),
    ("add_security_headers", "headers", "x-frame-options"),
    ("cache_everything", "not_cached", "served from the edge's cache"),
    ("waf_markup", "not_blocked", "Markdown with HTML-like text"),
    ("waf_saml", "not_blocked", "SAML sign-in"),
    ("rate_limit_api", "burst", "stopped by the edge"),
])
def test_each_mistake_is_caught(origin, fault, key, needle):
    edge = _edge(origin, faults={fault})
    try:
        checks = edge_check.run(edge.url, origin_url=origin, burst=8)
    finally:
        edge.close()
    check = _by_key(checks)[key]
    assert edge_check.failed(checks) or check.status in ("FAIL", "WARN"), edge_check.report(checks)
    assert needle.lower() in (check.detail + str(check.evidence)).lower(), check


def test_an_origin_open_around_the_edge_and_no_compression(origin, monkeypatch):
    monkeypatch.setattr(get_settings(), "edge_secret", None)
    edge = StubEdge(origin, faults={"no_compression"})
    try:
        checks = _by_key(edge_check.run(edge.url, origin_url=origin, burst=3))
    finally:
        edge.close()
    assert checks["origin_lockdown"].status == "FAIL" and checks["compression"].status == "WARN"


def test_the_echo_endpoint_says_how_the_request_was_seen(client):
    seen = client.get("/api/v1/edge/echo", headers={"CF-Ray": "abc-IAD"}).json()
    assert seen["edge_headers"] == ["cf-ray"] and seen["edge_trusted"] is False and seen["request_id"]
