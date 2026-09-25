# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Outbound HTTP to addresses an organization's admin typed in (today: the OIDC issuer an
SSO connection points at, and whatever endpoints its discovery document names).

Without this, anyone who can edit an SSO connection can make the OpenClear server send
requests into its own network: cloud metadata services (169.254.169.254), databases and
admin consoles on private addresses, or localhost. That's server-side request forgery
(SSRF), and /ui/login/sso/{id}/start is unauthenticated, so once a connection exists anyone
can trigger the fetch.

Two layers:
- check_url(): the URL itself must be https (no embedded credentials) and must not name
  localhost or a non-public IP literal. Used when a connection is saved (a clear form
  error) and before every fetch.
- public_only_client(): an httpx client whose connections resolve the hostname and refuse
  to connect unless EVERY address it resolves to is public, then connect to exactly the
  address that was checked. Checking at connect time, not beforehand, is what defeats DNS
  rebinding (a name that resolves to a public address when checked and a private one when
  used). TLS certificates are still verified against the hostname (httpcore sends the
  original host as SNI). Redirects aren't followed, since every hop would need the same
  checks. The client doesn't use proxy environment variables (httpx ignores them when an
  explicit transport is given), so a deployment that needs an egress proxy to reach its
  identity provider needs a follow-up here.

config.Settings.oidc_allow_private_hosts turns both layers off so a local test identity
provider (http://localhost:8080, a Keycloak container) works. config.assert_production_safe
refuses to start a production deployment with it on."""

import ipaddress
import socket
from urllib.parse import urlsplit

import httpcore
import httpx

from openclear.config import get_settings


class UnsafeUrlError(ValueError):
    """The URL (or an address it resolves to) isn't allowed as an outbound destination.
    The message is safe to show an admin on a settings form."""


def _allow_private() -> bool:
    return get_settings().oidc_allow_private_hosts


def _is_public_ip(address: str) -> bool:
    ip = ipaddress.ip_address(address.split("%", 1)[0])  # drop an IPv6 zone id
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


def check_url(url: str, *, what: str = "URL") -> str:
    """Returns the URL unchanged if it's an acceptable outbound destination, else raises
    UnsafeUrlError. Does no DNS lookup (see public_only_client for that)."""
    parts = urlsplit(url)
    allow_private = _allow_private()
    if parts.scheme != "https" and not (allow_private and parts.scheme == "http"):
        raise UnsafeUrlError(f"The {what} must start with https://")
    if parts.username or parts.password:
        raise UnsafeUrlError(f"The {what} must not contain a username or password")
    host = (parts.hostname or "").rstrip(".").lower()
    if not host:
        raise UnsafeUrlError(f"The {what} must include a host name")
    if allow_private:
        return url
    if host == "localhost" or host.endswith(".localhost"):
        raise UnsafeUrlError(f"The {what} must be a public address, not localhost")
    try:
        literal_ip = ipaddress.ip_address(host)
    except ValueError:
        literal_ip = None
    if literal_ip is not None and not _is_public_ip(host):
        raise UnsafeUrlError(f"The {what} must be a public address, not a private or internal one")
    return url


class _PublicOnlyBackend(httpcore.SyncBackend):
    def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        try:
            infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        except OSError as exc:
            raise httpcore.ConnectError(f"Could not resolve {host}: {exc}") from exc
        addresses = [info[4][0] for info in infos]
        if not addresses or not all(_is_public_ip(address) for address in addresses):
            raise httpcore.ConnectError(f"Refusing to connect to {host}: it resolves to a non-public address")
        return super().connect_tcp(
            addresses[0], port, timeout=timeout, local_address=local_address, socket_options=socket_options
        )


class _PublicOnlyTransport(httpx.HTTPTransport):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._pool._network_backend = _PublicOnlyBackend()


def public_only_transport() -> httpx.BaseTransport:
    """The transport for any outbound request to an admin-configured address; a plain one
    when oidc_allow_private_hosts is on."""
    return httpx.HTTPTransport() if _allow_private() else _PublicOnlyTransport()


def public_only_client(**kwargs) -> httpx.Client:
    return httpx.Client(transport=public_only_transport(), follow_redirects=False, **kwargs)
