# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

from starlette.requests import Request

from openclear.config import get_settings
from openclear.web.client_ip import get_client_ip


def _make_request(client_host, forwarded_for=None):
    headers = []
    if forwarded_for is not None:
        headers.append((b"x-forwarded-for", forwarded_for.encode()))
    scope = {"type": "http", "headers": headers, "client": (client_host, 12345) if client_host else None}
    return Request(scope)


def test_defaults_to_the_direct_connection_ip(monkeypatch):
    monkeypatch.setattr(get_settings(), "trusted_proxy_count", 0)
    request = _make_request("203.0.113.5", forwarded_for="10.0.0.1")

    assert get_client_ip(request) == "203.0.113.5"


def test_ignores_a_forwarded_header_when_no_proxy_is_configured(monkeypatch):
    monkeypatch.setattr(get_settings(), "trusted_proxy_count", 0)
    # A client can set X-Forwarded-For to anything it wants -- with trusted_proxy_count
    # still at its default of 0, this must never be trusted.
    request = _make_request("203.0.113.5", forwarded_for="1.2.3.4")

    assert get_client_ip(request) == "203.0.113.5"


def test_trusts_exactly_the_configured_hop_count(monkeypatch):
    monkeypatch.setattr(get_settings(), "trusted_proxy_count", 1)
    # X-Forwarded-For: <client-claimed>, <our trusted proxy's own appended hop> -- with
    # one trusted hop, the rightmost entry is the real client, never the spoofable left one.
    request = _make_request("10.0.0.1", forwarded_for="203.0.113.9, 10.0.0.2")

    assert get_client_ip(request) == "10.0.0.2"


def test_falls_back_to_direct_connection_when_header_has_fewer_hops_than_configured(monkeypatch):
    monkeypatch.setattr(get_settings(), "trusted_proxy_count", 2)
    request = _make_request("10.0.0.1", forwarded_for="203.0.113.9")

    assert get_client_ip(request) == "10.0.0.1"


def test_returns_none_when_there_is_no_connection_and_no_trusted_header(monkeypatch):
    monkeypatch.setattr(get_settings(), "trusted_proxy_count", 0)
    request = _make_request(None)

    assert get_client_ip(request) is None
