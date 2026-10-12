# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Security event delivery (SIEM_PLAN.md S3, S5; services/siem/): the wire formats, and
delivery to fake receivers in the suite (a TLS syslog collector, a Splunk HEC endpoint and
an HTTPS collector) through outages, retries and catch-up, kept to each bank's own events."""

import datetime as dt
import json
import socket
import ssl
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from sqlalchemy import select

from openclear.config import get_settings
from openclear.domain.security_event import SecurityEvent
from openclear.domain.security_event_destination import SecurityEventDestination
from openclear.services import controls_service, housekeeping_service, security_events
from openclear.services.siem import delivery, formats, senders
from tests.conftest import TenantFactory

RECORD = {
    "id": "evt_7", "sequence": 7, "time": "2026-10-11T14:03:22.118000+00:00",
    "event": {"name": "auth.login.failed", "category": "authentication", "outcome": "failure", "severity": "medium",
              "reason": "invalid_credentials"},
    "bank": {"id": "b1", "slug": "acme|bank"},
    "actor": {"type": "user", "id": "u1", "email": "a=b@acme.example"},
    "source": {"ip": "203.0.113.7", "user_agent": "curl\\8\nline two"},
    "request": {"id": "r1", "channel": "web"},
    "details": {"attempts": 3},
}


# --- Formats -----------------------------------------------------------------------------------


def test_cef_escapes_its_header_and_values():
    line = formats.to_cef(RECORD)
    header, extension = line.split("|auth.login.failed (failure)|5|", 1)  # the seventh field, severity, ends the header
    assert header.startswith("CEF:0|OpenClear|OpenClear|") and header.endswith("|auth.login.failed")
    assert "suser=a\\=b@acme.example" in extension  # "=" escaped in a value
    assert "cs1=acme|bank" in extension  # a pipe needs no escape in the extension
    assert "requestClientApplication=curl\\\\8\\nline two" in extension  # backslash and newline
    assert "\n" not in line


def test_leef_keeps_one_attribute_per_tab():
    line = formats.to_leef(RECORD)
    assert line.startswith("LEEF:2.0|OpenClear|OpenClear|") and "|auth.login.failed|x09|" in line
    attributes = dict(part.split("=", 1) for part in line.split("|x09|", 1)[1].split("\t"))
    assert attributes["usrName"] == "a=b@acme.example"
    assert attributes["src"] == "203.0.113.7" and attributes["sev"] == "5"
    assert attributes["requestClientApplication"] == "curl\\8 line two"  # the line break can't split the record


def test_rfc5424_message_and_octet_counting():
    message = formats.syslog_message(RECORD, "json", hostname="oc-host")
    assert message.startswith("<109>1 2026-10-11T14:03:22.118000+00:00 oc-host openclear - auth.login.failed - {")  # 13*8+5
    assert json.loads(message.split(" - ", 2)[2]) == RECORD
    framed = formats.octet_frame("héllo")
    assert framed == b"6 h\xc3\xa9llo"  # the count is bytes, not characters


# --- Fake receivers ----------------------------------------------------------------------------


class _HttpReceiver:
    """A Splunk HEC endpoint or an HTTPS collector (plain http: private hosts are allowed here)."""

    def __init__(self, *, ack: bool = False) -> None:
        self.requests: list[dict] = []
        self.status = 200
        receiver = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                body = self.rfile.read(int(self.headers["content-length"]))
                receiver.requests.append({"path": self.path, "headers": {k.lower(): v for k, v in self.headers.items()}, "body": body})
                if receiver.status != 200:
                    self.send_response(receiver.status)
                    self.end_headers()
                    return
                reply = {"text": "Success", "code": 0}
                if self.path.endswith("/ack"):
                    reply = {"acks": {"0": True}}
                elif ack:
                    reply["ackId"] = 0
                data = json.dumps(reply).encode()
                self.send_response(200)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def events(self) -> list[dict]:
        records = []
        for request in self.requests:
            if request["path"].endswith("/ack"):
                continue
            body = request["body"].decode()
            if request["path"].startswith("/services/collector"):
                records += [json.loads(line)["event"] for line in body.splitlines() if line]
            else:
                records += json.loads(body)
        return records

    def close(self) -> None:
        self.server.shutdown()


def _self_signed(tmp_path):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(days=1))
            .not_valid_after(now + timedelta(days=1))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost")]), critical=False)
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
            .sign(key, hashes.SHA256()))
    cert_path, key_path = tmp_path / "syslog.crt", tmp_path / "syslog.key"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    return cert_path, key_path


class _TlsSyslogReceiver:
    """RFC 5425: syslog over TLS, octet-counted."""

    def __init__(self, tmp_path) -> None:
        self.cert_path, key_path = _self_signed(tmp_path)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(self.cert_path, key_path)
        self.messages: list[str] = []
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen()
        self.port = self.sock.getsockname()[1]
        self._context = context
        self._stop = False
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self) -> None:
        while not self._stop:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            try:
                with self._context.wrap_socket(conn, server_side=True) as tls:
                    data = b""
                    while chunk := tls.recv(65536):
                        data += chunk
            except (OSError, ssl.SSLError):
                continue
            while data:
                length, _, rest = data.partition(b" ")
                size = int(length)
                self.messages.append(rest[:size].decode())
                data = rest[size:]

    def close(self) -> None:
        self._stop = True
        self.sock.close()


@pytest.fixture
def siem_settings(monkeypatch, engine):
    # After `engine`: setting up the Postgres test database resets the settings cache.
    settings = get_settings()
    monkeypatch.setattr(settings, "siem_allow_private_hosts", True)
    monkeypatch.setattr(settings, "siem_settle_seconds", 0)
    monkeypatch.setattr(settings, "siem_timeout_seconds", 5)
    return settings


@pytest.fixture
def http_receiver(siem_settings):
    r = _HttpReceiver()
    yield r
    r.close()


def _emit(db_session, tenant_id, name="auth.login.failed", **kwargs):
    event = security_events.emit(db_session, name, tenant_id=tenant_id, **kwargs)
    db_session.commit()
    return event


def _later() -> datetime:
    return datetime.now(timezone.utc) + timedelta(seconds=2)


# --- Delivery ----------------------------------------------------------------------------------


def test_a_banks_destination_gets_its_own_events_in_order_and_only_once(http_receiver, db_session, tenant_factory):
    tenant, _account, _users = tenant_factory.make(slug="siem-own")
    other, _account, _users = tenant_factory.make(slug="siem-other")
    delivery.save_bank_destination(db_session, tenant.id, kind="https", enabled=True, url=http_receiver.url + "/events", token="tok-1")
    db_session.commit()
    first = _emit(db_session, tenant.id, reason="invalid_credentials")
    _emit(db_session, other.id)  # another bank's: never sent here
    second = _emit(db_session, tenant.id, "auth.lockout")

    assert delivery.deliver_due(db_session, now=_later()) == 2
    received = http_receiver.events()
    assert [r["sequence"] for r in received] == [first.id, second.id]
    assert {r["bank"]["slug"] for r in received} == {"siem-own"}
    assert http_receiver.requests[0]["headers"]["authorization"] == "Bearer tok-1"
    assert delivery.deliver_due(db_session, now=_later()) == 0  # the cursor moved on
    destination = delivery.bank_destination(db_session, tenant.id)
    assert destination.cursor == second.id and destination.delivered_count == 2 and destination.last_success_at


def test_an_outage_keeps_the_cursor_backs_off_and_catches_up(http_receiver, db_session, tenant_factory):
    tenant, _account, _users = tenant_factory.make(slug="siem-outage")
    delivery.save_bank_destination(db_session, tenant.id, kind="https", enabled=True, url=http_receiver.url)
    db_session.commit()
    _emit(db_session, tenant.id)
    http_receiver.status = 503

    now = _later()
    assert delivery.deliver_due(db_session, now=now) == 0
    destination = delivery.bank_destination(db_session, tenant.id)
    assert destination.cursor == 0 and destination.consecutive_failures == 1
    assert "503" in destination.last_error and destination.failing_since is not None
    attempts = len(http_receiver.requests)
    assert delivery.deliver_due(db_session, now=now + timedelta(seconds=1)) == 0
    assert len(http_receiver.requests) == attempts  # waiting out the backoff, not hammering it

    # Back up: everything waiting goes, in order, and the failure is cleared.
    http_receiver.status = 200
    _emit(db_session, tenant.id, "auth.lockout")
    assert delivery.deliver_due(db_session, now=now + timedelta(minutes=1)) == 2
    db_session.refresh(destination)
    assert destination.failing_since is None and destination.consecutive_failures == 0 and destination.last_error is None


def test_splunk_hec_with_acknowledgement(siem_settings, db_session, tenant_factory):
    receiver = _HttpReceiver(ack=True)
    try:
        tenant, _account, _users = tenant_factory.make(slug="siem-hec")
        delivery.save_bank_destination(db_session, tenant.id, kind="splunk_hec", enabled=True, url=receiver.url, token="hec-token",
                                       splunk_index="security", splunk_sourcetype="openclear:security")
        db_session.commit()
        event = _emit(db_session, tenant.id)
        assert delivery.deliver_due(db_session, now=_later()) == 1
        post = receiver.requests[0]
        assert post["path"] == "/services/collector/event"
        assert post["headers"]["authorization"] == "Splunk hec-token" and post["headers"]["x-splunk-request-channel"]
        line = json.loads(post["body"].decode().splitlines()[0])
        assert line["index"] == "security" and line["sourcetype"] == "openclear:security" and line["event"]["sequence"] == event.id
        assert receiver.requests[1]["path"] == "/services/collector/ack"  # delivered only once Splunk says it's indexed
    finally:
        receiver.close()


def test_the_installations_syslog_destination_over_tls_gets_every_bank(siem_settings, monkeypatch, tmp_path, db_session, tenant_factory):
    receiver = _TlsSyslogReceiver(tmp_path)
    try:
        for name, value in {"siem_kind": "syslog", "siem_host": "localhost", "siem_port": receiver.port, "siem_transport": "tls",
                            "siem_format": "cef", "siem_tls_ca_file": str(receiver.cert_path)}.items():
            monkeypatch.setattr(siem_settings, name, value)
        one, _a, _u = tenant_factory.make(slug="siem-syslog-one")
        two, _a, _u = tenant_factory.make(slug="siem-syslog-two")
        _emit(db_session, one.id)
        _emit(db_session, two.id, "auth.lockout")
        _emit(db_session, None, reason="unknown_bank")  # belongs to no bank: the installation's still gets it

        assert delivery.deliver_due(db_session, now=_later()) >= 3
        for _ in range(50):
            if len(receiver.messages) >= 3:
                break
            threading.Event().wait(0.05)
        mine = [m for m in receiver.messages if "siem-syslog-" in m or "unknown_bank" in m]
        assert len(mine) == 3
        assert all(m.startswith("<1") and " openclear - " in m and "CEF:0|OpenClear|OpenClear|" in m for m in mine)
        row = db_session.execute(select(SecurityEventDestination).where(SecurityEventDestination.tenant_id.is_(None))).scalar_one()
        assert row.kind == "syslog" and row.last_success_at is not None
    finally:
        receiver.close()


def test_a_tls_destination_with_an_untrusted_certificate_fails_clearly(siem_settings, monkeypatch, tmp_path, db_session, tenant_factory):
    receiver = _TlsSyslogReceiver(tmp_path)
    try:
        for name, value in {"siem_kind": "syslog", "siem_host": "localhost", "siem_port": receiver.port, "siem_transport": "tls"}.items():
            monkeypatch.setattr(siem_settings, name, value)  # no CA file: the self-signed certificate isn't trusted
        tenant, _a, _u = tenant_factory.make(slug="siem-untrusted")
        _emit(db_session, tenant.id)
        delivery.deliver_due(db_session, now=_later())
        row = db_session.execute(select(SecurityEventDestination).where(SecurityEventDestination.tenant_id.is_(None))).scalar_one()
        assert row.cursor == 0 and "TLS" in row.last_error
    finally:
        receiver.close()


def test_bank_destinations_must_be_public_unless_allowed(monkeypatch, engine, db_session, tenant_factory):
    monkeypatch.setattr(get_settings(), "siem_allow_private_hosts", False)
    tenant, _account, _users = tenant_factory.make(slug="siem-ssrf")
    for kwargs in ({"kind": "syslog", "host": "10.0.0.5"}, {"kind": "syslog", "host": "localhost"},
                   {"kind": "https", "url": "http://collector.example"}, {"kind": "https", "url": "https://169.254.169.254/x"},
                   {"kind": "syslog", "host": "siem.example", "transport": "udp"}, {"kind": "splunk_hec", "url": "https://splunk.example"}):
        with pytest.raises(delivery.DestinationError):
            delivery.save_bank_destination(db_session, tenant.id, enabled=True, **kwargs)
        db_session.rollback()
    # A name is accepted when saved; its addresses are checked when connecting.
    destination = delivery.save_bank_destination(db_session, tenant.id, kind="syslog", enabled=True, host="siem.example", port="6514")
    with pytest.raises(senders.DeliveryError, match="non-public|resolve"):
        senders.send_syslog(senders.Target(kind="syslog", host="localhost", port=6514), [RECORD], hostname="h")
    assert destination.port == 6514 and destination.transport == "tls"


def test_a_destinations_description_leaves_out_keys_in_its_url(http_receiver, db_session, tenant_factory):
    tenant, _account, _users = tenant_factory.make(slug="siem-describe")
    destination = delivery.save_bank_destination(db_session, tenant.id, kind="https", enabled=True,
                                                 url=http_receiver.url + "/ingest?code=very-secret")
    assert delivery.describe(destination) == f"HTTPS (JSON) {http_receiver.url}/ingest"


def test_the_token_is_stored_encrypted_and_kept_when_left_blank(http_receiver, db_session, tenant_factory):
    tenant, _account, _users = tenant_factory.make(slug="siem-token")
    destination = delivery.save_bank_destination(db_session, tenant.id, kind="splunk_hec", enabled=True, url=http_receiver.url, token="s3cret-hec")
    assert "s3cret-hec" not in destination.token_encrypted
    delivery.save_bank_destination(db_session, tenant.id, kind="splunk_hec", enabled=True, url=http_receiver.url, token="")
    assert delivery.target_for(destination).token == "s3cret-hec"


def test_housekeeping_keeps_events_a_destination_has_not_had(http_receiver, monkeypatch, db_session, tenant_factory):
    monkeypatch.setattr(get_settings(), "security_event_retention_days", 1)
    tenant, _account, _users = tenant_factory.make(slug="siem-keep")
    other, _account, _users = tenant_factory.make(slug="siem-keep-other")
    delivery.save_bank_destination(db_session, tenant.id, kind="https", enabled=True, url=http_receiver.url)
    db_session.commit()
    waiting = _emit(db_session, tenant.id)
    unwatched = _emit(db_session, other.id)  # no destination receives it
    old = datetime.now(timezone.utc) - timedelta(days=3)
    for event in (waiting, unwatched):
        event.occurred_at = old
    db_session.commit()

    housekeeping_service.run(db_session)
    remaining = set(db_session.execute(select(SecurityEvent.id)).scalars())
    assert waiting.id in remaining and unwatched.id not in remaining

    delivery.deliver_due(db_session, now=_later())
    housekeeping_service.run(db_session)
    assert waiting.id not in set(db_session.execute(select(SecurityEvent.id)).scalars())


# --- The control and the evidence ----------------------------------------------------------------


def _control(db_session, tenant_id):
    return next(c for c in controls_service.status(db_session, tenant_id) if c.key == "event_delivery")


def test_the_control_warns_fails_and_passes(http_receiver, db_session, tenant_factory):
    tenant, _account, _users = tenant_factory.make(slug="siem-control")
    assert _control(db_session, tenant.id).status == "WARN"  # nothing set, and nobody reads the feed

    delivery.mark_feed_read(db_session, tenant.id)
    db_session.commit()
    assert _control(db_session, tenant.id).status == "PASS"

    destination = delivery.save_bank_destination(db_session, tenant.id, kind="https", enabled=True, url=http_receiver.url)
    db_session.commit()
    _emit(db_session, tenant.id)
    http_receiver.status = 500
    delivery.deliver_due(db_session, now=_later())
    destination.failing_since = datetime.now(timezone.utc) - timedelta(hours=2)
    db_session.commit()
    control = _control(db_session, tenant.id)
    assert control.status == "FAIL" and "500" in control.detail

    http_receiver.status = 200
    destination.next_attempt_at = None
    db_session.commit()
    delivery.deliver_due(db_session, now=_later())
    control = _control(db_session, tenant.id)
    assert control.status == "PASS" and "last delivered" in control.detail


def test_the_evidence_export_counts_events_by_category(http_receiver, db_session, tenant_factory):
    from openclear.services import evidence_service

    tenant, _account, users = tenant_factory.make(slug="siem-evidence")
    delivery.save_bank_destination(db_session, tenant.id, kind="https", enabled=True, url=http_receiver.url)
    db_session.commit()
    _emit(db_session, tenant.id)
    _emit(db_session, tenant.id)
    _emit(db_session, tenant.id, "access.denied")
    delivery.deliver_due(db_session, now=_later())
    today = dt.date.today()
    bundle = evidence_service.build(db_session, tenant.id, start=today - dt.timedelta(days=1), end=today + dt.timedelta(days=1),
                                    generated_by_user_id=users["admin"].id)
    section = bundle["security_events"]
    assert section["counts_by_category"]["authentication"] >= 2 and section["counts_by_category"]["access"] >= 1
    assert section["destinations"][0]["delivered_total"] >= 3 and section["destinations"][0]["failing_since"] is None


# --- The tile ----------------------------------------------------------------------------------


def _login(client, slug, email):
    client.get("/ui/login")
    csrf = client.cookies.get("csrf_token")
    client.post("/ui/login", data={"tenant_slug": slug, "email": email, "password": TenantFactory.PASSWORD, "csrf_token": csrf, "next": "/ui/"})
    return csrf


def test_a_bank_adds_a_destination_and_sends_a_test_event(http_receiver, client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="siem-web")
    csrf = _login(client, tenant.slug, users["admin"].email)
    resp = client.post("/ui/settings/security-events", data={
        "csrf_token": csrf, "action": "test", "kind": "https", "enabled": "true", "url": http_receiver.url + "/in", "token": "abc",
    }, follow_redirects=False)
    assert resp.status_code == 303 and "test+event+was+delivered" in resp.headers["location"].replace("%20", "+").lower()
    assert http_receiver.events()[0]["event"]["name"] == "security_events.test"
    panel = client.get("/ui/settings").text
    assert "HTTPS (JSON)" in panel

    # A failing test shows the error with the form, and the setting is still saved.
    http_receiver.status = 401
    resp = client.post("/ui/settings/security-events", data={
        "csrf_token": csrf, "action": "test", "kind": "https", "enabled": "true", "url": http_receiver.url + "/in"})
    assert resp.status_code == 422 and "HTTP 401" in resp.text and 'id="settings-section"' in resp.text
    changed = [e for e in db_session.execute(select(SecurityEvent).where(SecurityEvent.tenant_id == tenant.id)).scalars()
               if e.name == "security_events.destination.changed"]
    assert changed and all("abc" not in json.dumps(security_events.as_record(e)) for e in changed)

    resp = client.post("/ui/settings/security-events", data={"csrf_token": csrf, "action": "remove"}, follow_redirects=False)
    assert resp.status_code == 303
    assert delivery.bank_destination(db_session, tenant.id) is None
