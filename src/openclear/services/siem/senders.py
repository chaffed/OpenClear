# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Senders for one batch of records. Each raises DeliveryError (or any exception) when the
batch wasn't accepted, so the caller keeps its cursor and retries the same batch later.

A bank's destination is an address its admin typed in, so it's held to the same rules as
webhooks and SSO (auth/outbound_http.py): public addresses only, checked when the
connection is made, unless siem_allow_private_hosts is on. The installation's destination
comes from the operator's own configuration and may be private."""

import json
import socket
import ssl
import time
import uuid
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

import httpx

from openclear.auth.outbound_http import UnsafeUrlError, _is_public_ip, _PublicOnlyTransport, check_url
from openclear.services.siem import formats

KINDS = {"syslog": "Syslog (RFC 5424)", "splunk_hec": "Splunk HTTP Event Collector", "https": "HTTPS (JSON)"}
TRANSPORTS = ("tls", "tcp", "udp")


class DeliveryError(RuntimeError):
    """The destination refused or didn't answer; the message is fit to show an admin."""


@dataclass
class Target:
    """What a sender needs, from a destination row or from configuration."""

    kind: str
    host: str | None = None
    port: int | None = None
    transport: str = "tls"
    format: str = "json"
    url: str | None = None
    token: str | None = None
    splunk_index: str | None = None
    splunk_sourcetype: str | None = None
    channel: str | None = None  # Splunk's request channel, for acknowledgement
    allow_private: bool = False
    ca_file: str | None = None
    verify: bool = True
    timeout: float = 10.0


def check_host(host: str, *, allow_private: bool) -> None:
    """For saving a syslog destination: a host name or a public address literal."""
    host = (host or "").strip().rstrip(".").lower()
    if not host:
        raise UnsafeUrlError("The syslog host is required")
    if allow_private:
        return
    if host == "localhost" or host.endswith(".localhost"):
        raise UnsafeUrlError("The syslog host must be a public address, not localhost")
    try:
        public = _is_public_ip(host)
    except ValueError:
        return  # a name; its addresses are checked when connecting
    if not public:
        raise UnsafeUrlError("The syslog host must be a public address, not a private or internal one")


def _resolve(host: str, port: int, kind: int, *, allow_private: bool) -> list[tuple]:
    """Every address for the host; unless private hosts are allowed, all must be public.
    The connection is then made to one of exactly these (not a second lookup), which is
    what defeats DNS rebinding."""
    try:
        infos = socket.getaddrinfo(host, port, type=kind)
    except OSError as exc:
        raise DeliveryError(f"Couldn't resolve {host}: {exc}") from exc
    if not infos:
        raise DeliveryError(f"Couldn't resolve {host}")
    if not allow_private and not all(_is_public_ip(info[4][0]) for info in infos):
        raise DeliveryError(f"Refusing to connect to {host}: it resolves to a non-public address")
    return infos


def _connect(infos: list[tuple], timeout: float, what: str) -> socket.socket:
    error: OSError | None = None
    for family, socktype, proto, _name, address in infos:
        sock = socket.socket(family, socktype, proto)
        sock.settimeout(timeout)
        try:
            sock.connect(address)
            return sock
        except OSError as exc:
            sock.close()
            error = exc
    raise DeliveryError(f"Couldn't connect to {what}: {error}")


def _tls_context(target: Target) -> ssl.SSLContext:
    context = ssl.create_default_context(cafile=target.ca_file or None)
    context.minimum_version = ssl.TLSVersion.TLSv1_2  # ASVS 5.0 12.1.1, whatever the system default
    if not target.verify:
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    return context


def send_syslog(target: Target, records: list[dict], *, hostname: str) -> None:
    """One connection per batch. TLS uses octet counting (RFC 5425), plain TCP a newline
    after each message (RFC 6587); UDP sends one message per datagram, for a relay on the
    same host."""
    messages = [formats.syslog_message(r, target.format, hostname=hostname) for r in records]
    port = target.port or (6514 if target.transport == "tls" else 514)
    if target.transport == "udp":
        family, socktype, proto, _name, address = _resolve(target.host, port, socket.SOCK_DGRAM, allow_private=target.allow_private)[0]
        with socket.socket(family, socktype, proto) as sock:
            sock.settimeout(target.timeout)
            for message in messages:
                sock.sendto(message.encode("utf-8")[:65000], address)
        return
    infos = _resolve(target.host, port, socket.SOCK_STREAM, allow_private=target.allow_private)
    raw = _connect(infos, target.timeout, f"{target.host}:{port}")
    try:
        conn = _tls_context(target).wrap_socket(raw, server_hostname=target.host) if target.transport == "tls" else raw
        with conn:
            # TLS: octet counting, as RFC 5425 requires. Plain TCP: one message per line
            # (RFC 6587's traditional framing), which Splunk's TCP input, QRadar and most
            # relays expect; a message never contains a line break.
            if target.transport == "tls":
                conn.sendall(b"".join(formats.octet_frame(m) for m in messages))
            else:
                conn.sendall(b"".join(m.encode("utf-8") + b"\n" for m in messages))
            if target.transport == "tls":
                # Close cleanly, so a receiver that's still reading sees the whole batch.
                try:
                    conn.unwrap()
                except (OSError, ssl.SSLError):
                    pass
    except ssl.SSLError as exc:
        raise DeliveryError(f"TLS with {target.host}:{port} failed: {exc.reason or exc}") from exc
    except OSError as exc:
        raise DeliveryError(f"Sending to {target.host}:{port} failed: {exc}") from exc
    finally:
        raw.close()


def hec_url(url: str) -> str:
    """The event endpoint: a bare https://host:8088 gets /services/collector/event."""
    parts = urlsplit(url.strip())
    if parts.path in ("", "/"):
        parts = parts._replace(path="/services/collector/event")
    return urlunsplit(parts)


def _client(target: Target) -> httpx.Client:
    """Public addresses only unless allowed (auth/outbound_http.py), trusting the
    installation's CA file when it names one."""
    verify = _tls_context(target)  # always our context, so HEC gets the TLS 1.2 minimum too
    transport = httpx.HTTPTransport(verify=verify) if target.allow_private else _PublicOnlyTransport(verify=verify)
    return httpx.Client(transport=transport, follow_redirects=False, timeout=target.timeout)


def _check_status(response, what: str) -> None:
    if not 200 <= response.status_code < 300:
        detail = response.text[:200].strip()
        raise DeliveryError(f"{what} answered HTTP {response.status_code}" + (f": {detail}" if detail else ""))


def send_splunk_hec(target: Target, records: list[dict], *, hostname: str) -> None:
    """Events in one request, each with its time, host, source and type. When the token
    has indexer acknowledgement on, the batch counts as delivered only once Splunk says it
    was indexed."""
    url = hec_url(check_url(target.url or "", what="Splunk HEC URL", allow_private=target.allow_private))
    lines = []
    for record in records:
        event = {"event": record, "host": hostname, "source": "openclear", "sourcetype": target.splunk_sourcetype or "openclear:security"}
        if target.splunk_index:
            event["index"] = target.splunk_index
        try:
            from datetime import datetime

            event["time"] = round(datetime.fromisoformat(record["time"]).timestamp(), 3)
        except (KeyError, TypeError, ValueError):
            pass
        lines.append(json.dumps(event, separators=(",", ":"), default=str))
    channel = target.channel or str(uuid.uuid4())
    headers = {"authorization": f"Splunk {target.token or ''}", "content-type": "application/json",
               "x-splunk-request-channel": channel, "user-agent": "OpenClear-SIEM"}
    with _client(target) as client:
        response = client.post(url, content="\n".join(lines).encode(), headers=headers)
        _check_status(response, "Splunk")
        try:
            ack_id = response.json().get("ackId")
        except ValueError:
            ack_id = None
        if ack_id is None:
            return
        ack_url = urlunsplit(urlsplit(url)._replace(path="/services/collector/ack"))
        deadline = time.monotonic() + target.timeout
        while time.monotonic() < deadline:
            ack = client.post(ack_url, json={"acks": [ack_id]}, headers=headers)
            _check_status(ack, "Splunk's acknowledgement endpoint")
            if (ack.json().get("acks") or {}).get(str(ack_id)):
                return
            time.sleep(0.5)
        raise DeliveryError("Splunk accepted the events but didn't confirm they were indexed in time")


def send_https(target: Target, records: list[dict], *, hostname: str) -> None:
    """A JSON array of records in one POST; a token is sent as a bearer token."""
    url = check_url(target.url or "", what="HTTPS URL", allow_private=target.allow_private)
    headers = {"content-type": "application/json", "user-agent": "OpenClear-SIEM"}
    if target.token:
        headers["authorization"] = f"Bearer {target.token}"
    body = json.dumps(records, separators=(",", ":"), default=str).encode()
    with _client(target) as client:
        _check_status(client.post(url, content=body, headers=headers), "The collector")


def send(target: Target, records: list[dict], *, hostname: str | None = None) -> None:
    hostname = hostname or socket.gethostname()
    {"syslog": send_syslog, "splunk_hec": send_splunk_hec, "https": send_https}[target.kind](target, records, hostname=hostname)
