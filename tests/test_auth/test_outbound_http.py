# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""SSRF protection for admin-configured outbound URLs (auth/outbound_http.py)."""

import socket

import httpcore
import httpx
import pytest

from openclear.auth.outbound_http import UnsafeUrlError, check_url, public_only_client
from openclear.config import get_settings


@pytest.mark.parametrize(
    "url",
    [
        "https://idp.example.com",
        "https://login.microsoftonline.com/tenant-id/v2.0",
        "https://8.8.8.8/realm",
    ],
)
def test_public_https_urls_are_allowed(url):
    assert check_url(url) == url


@pytest.mark.parametrize(
    ("url", "reason"),
    [
        ("http://idp.example.com", "https"),
        ("ftp://idp.example.com", "https"),
        ("https://user:pass@idp.example.com", "username or password"),
        ("https://localhost/realm", "localhost"),
        ("https://auth.localhost", "localhost"),
        ("https://127.0.0.1", "private or internal"),
        ("https://10.1.2.3", "private or internal"),
        ("https://192.168.0.10", "private or internal"),
        ("https://172.16.5.5", "private or internal"),
        ("https://169.254.169.254/latest/meta-data", "private or internal"),  # cloud metadata
        ("https://100.64.0.1", "private or internal"),  # carrier-grade NAT
        ("https://[::1]", "private or internal"),
        ("https://[fd00::1]", "private or internal"),
        ("https://[::ffff:10.0.0.1]", "private or internal"),  # IPv4-mapped IPv6
        ("https://", "host name"),
    ],
)
def test_unsafe_urls_are_rejected(url, reason):
    with pytest.raises(UnsafeUrlError, match=reason):
        check_url(url)


def test_local_testing_flag_allows_http_localhost(monkeypatch):
    monkeypatch.setenv("OPENCLEAR_OIDC_ALLOW_PRIVATE_HOSTS", "true")
    get_settings.cache_clear()

    assert check_url("http://localhost:8080/realms/test") == "http://localhost:8080/realms/test"


def _resolve_to(monkeypatch, *addresses):
    def fake_getaddrinfo(host, port, *args, **kwargs):
        return [(socket.AF_INET6 if ":" in a else socket.AF_INET, socket.SOCK_STREAM, 6, "", (a, port)) for a in addresses]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)


@pytest.mark.parametrize("addresses", [("10.0.0.5",), ("169.254.169.254",), ("93.184.216.34", "127.0.0.1")])
def test_client_refuses_hostnames_that_resolve_to_non_public_addresses(monkeypatch, addresses):
    """DNS rebinding: the hostname looks harmless, but what it resolves to at connect time
    is what matters — and every resolved address must be public."""
    _resolve_to(monkeypatch, *addresses)
    connected = []
    monkeypatch.setattr(httpcore.SyncBackend, "connect_tcp", lambda self, host, port, **kw: connected.append(host))

    with public_only_client() as client, pytest.raises(httpx.ConnectError, match="non-public"):
        client.get("https://innocent-looking.example.com/.well-known/openid-configuration")
    assert connected == []


def test_client_connects_to_exactly_the_address_it_checked(monkeypatch):
    _resolve_to(monkeypatch, "93.184.216.34")
    connected = []

    def record(self, host, port, **kwargs):
        connected.append((host, port))
        raise httpcore.ConnectError("stop here — no real network in tests")

    monkeypatch.setattr(httpcore.SyncBackend, "connect_tcp", record)

    with public_only_client() as client, pytest.raises(httpx.ConnectError, match="stop here"):
        client.get("https://idp.example.com/.well-known/openid-configuration")
    assert connected == [("93.184.216.34", 443)]


def test_client_does_not_follow_redirects():
    def handler(request):
        return httpx.Response(302, headers={"location": "http://169.254.169.254/"})

    with public_only_client() as client:
        client._transport = httpx.MockTransport(handler)
        assert client.get("https://idp.example.com/x").status_code == 302


def test_sso_connection_with_internal_issuer_cannot_be_saved(db_session, tenant_factory):
    from openclear.domain.sso_connection import SsoProvider
    from openclear.services import sso_service

    tenant, _account, _users = tenant_factory.make(slug="ssrf-save")
    for issuer in ("http://idp.example.com", "https://169.254.169.254", "https://localhost:8443"):
        with pytest.raises(ValueError):
            sso_service.create_connection(
                db_session, tenant.id,
                sso_service.SsoConnectionInput(
                    provider=list(SsoProvider)[0], display_name="Evil", issuer=issuer, client_id="c",
                    client_secret="s", groups_claim_name="groups", auto_provision=False,
                ),
            )
