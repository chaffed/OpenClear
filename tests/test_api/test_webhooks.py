# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Webhooks (SDK_PLAN.md P0; services/webhook_service.py), delivered to a real local HTTP
receiver: Standard Webhooks signatures, thin payloads, retries, replays, and the outbound
URL checks."""

import base64
import hashlib
import hmac
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from sqlalchemy import select

from openclear.config import get_settings
from openclear.domain.decision import DecisionOutcome
from openclear.domain.webhook import WebhookDelivery, WebhookDeliveryStatus
from openclear.services import webhook_service
from openclear.workers import jobs
from tests.conftest import TenantFactory
from tests.items import _decide, _make_exception


class _Receiver:
    def __init__(self) -> None:
        self.requests: list[dict] = []
        self.status = 200
        receiver = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802 -- http.server's naming
                body = self.rfile.read(int(self.headers["content-length"]))
                receiver.requests.append({"headers": {k.lower(): v for k, v in self.headers.items()}, "body": body})
                self.send_response(receiver.status)
                self.end_headers()

            def log_message(self, *args):
                pass

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}/hooks"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.server.shutdown()


@pytest.fixture
def receiver(monkeypatch, engine):
    # After `engine`: setting up the Postgres test database resets the settings cache, which
    # would undo this.
    monkeypatch.setattr(get_settings(), "webhook_allow_private_hosts", True)  # the receiver is on localhost
    r = _Receiver()
    yield r
    r.close()


def _verify(secret: str, request: dict) -> bool:
    headers = request["headers"]
    key = base64.b64decode(secret.removeprefix("whsec_"))
    signed = f"{headers['webhook-id']}.{headers['webhook-timestamp']}.".encode() + request["body"]
    expected = base64.b64encode(hmac.new(key, signed, hashlib.sha256).digest()).decode()
    return any(sig == f"v1,{expected}" for sig in headers["webhook-signature"].split())


def test_events_are_delivered_signed_and_thin(receiver, db_session, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="hooks-deliver")
    endpoint, secret = webhook_service.create_endpoint(
        db_session, tenant.id, url=receiver.url, event_types=["exception.created", "exception.decided"],
    )
    db_session.commit()

    exception = _make_exception(db_session, tenant, account, users, "H1", "100.00", "999.00")
    _decide(db_session, tenant, users, exception, DecisionOutcome.RETURN)
    jobs.drain(db_session.get_bind())

    assert [json.loads(r["body"])["type"] for r in receiver.requests] == ["exception.created", "exception.decided"]
    created, decided = (json.loads(r["body"]) for r in receiver.requests)
    assert created["data"] == {"exception_id": str(exception.id), "network": "check", "customer_id": None}
    assert decided["data"]["sequence"] == 1 and decided["data"]["outcome"] == "return"
    assert all(_verify(secret, r) for r in receiver.requests)
    assert receiver.requests[0]["headers"]["webhook-id"].startswith("msg_")
    statuses = {d.status for d in db_session.execute(select(WebhookDelivery)).scalars()}
    assert statuses == {WebhookDeliveryStatus.SUCCEEDED}


def test_only_subscribed_active_endpoints_of_the_bank_get_events(receiver, db_session, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="hooks-filter")
    other, other_account, other_users = tenant_factory.make(slug="hooks-filter-other")
    webhook_service.create_endpoint(db_session, tenant.id, url=receiver.url, event_types=["bulk_upload.completed"])
    off, _ = webhook_service.create_endpoint(db_session, tenant.id, url=receiver.url, event_types=["exception.created"])
    off.is_active = False
    webhook_service.create_endpoint(db_session, other.id, url=receiver.url, event_types=["exception.created"])
    db_session.commit()

    _make_exception(db_session, tenant, account, users, "F1", "100.00", "999.00")
    jobs.drain(db_session.get_bind())
    assert receiver.requests == []


def test_failures_are_retried_on_the_schedule_then_marked_failed_and_replayable(receiver, db_session, tenant_factory):
    tenant, _account, _users = tenant_factory.make(slug="hooks-retry")
    endpoint, _secret = webhook_service.create_endpoint(db_session, tenant.id, url=receiver.url, event_types=["exception.created"])
    receiver.status = 503
    delivery = webhook_service.send_test(db_session, tenant.id, endpoint.id)
    db_session.commit()

    for attempt in range(1, len(webhook_service.RETRY_SCHEDULE) + 2):
        webhook_service.deliver(db_session, delivery.id)
        db_session.commit()
        if attempt <= len(webhook_service.RETRY_SCHEDULE):
            assert delivery.status == WebhookDeliveryStatus.PENDING and delivery.next_attempt_at is not None
    assert delivery.status == WebhookDeliveryStatus.FAILED and delivery.attempts == len(webhook_service.RETRY_SCHEDULE) + 1
    assert delivery.last_status_code == 503
    assert len({r["headers"]["webhook-id"] for r in receiver.requests}) == 1  # the same id on every retry

    receiver.status = 204
    webhook_service.replay(db_session, tenant.id, delivery.id)
    db_session.commit()
    jobs.drain(db_session.get_bind())
    db_session.refresh(delivery)
    assert delivery.status == WebhookDeliveryStatus.SUCCEEDED


def test_urls_must_be_public_https_unless_allowed(db_session, tenant_factory, monkeypatch):
    tenant, _account, _users = tenant_factory.make(slug="hooks-ssrf")
    monkeypatch.setattr(get_settings(), "webhook_allow_private_hosts", False)
    for url in ("http://example.com/hook", "https://127.0.0.1/hook", "https://169.254.169.254/latest", "https://user:pw@example.com/h"):
        with pytest.raises(webhook_service.WebhookError):
            webhook_service.create_endpoint(db_session, tenant.id, url=url, event_types=["exception.created"])
    with pytest.raises(webhook_service.WebhookError, match="Unknown event"):
        webhook_service.create_endpoint(db_session, tenant.id, url="https://example.com/h", event_types=["nope"])
    assert webhook_service.create_endpoint(db_session, tenant.id, url="https://example.com/h", event_types=["exception.created"])


def test_a_receiver_that_resolves_to_a_private_address_isnt_called(db_session, tenant_factory, monkeypatch):
    """The URL check passes a public-looking name; the connection-time check still refuses
    an address that turns out to be private (DNS rebinding)."""
    import socket

    tenant, _account, _users = tenant_factory.make(slug="hooks-rebind")
    monkeypatch.setattr(get_settings(), "webhook_allow_private_hosts", False)
    endpoint, _secret = webhook_service.create_endpoint(db_session, tenant.id, url="https://hooks.example.com/x", event_types=["exception.created"])
    delivery = webhook_service.send_test(db_session, tenant.id, endpoint.id)
    db_session.commit()
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", 443))])

    webhook_service.deliver(db_session, delivery.id)
    assert delivery.status == WebhookDeliveryStatus.PENDING and delivery.last_status_code is None and delivery.last_error


def test_the_admin_pages(receiver, client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="hooks-web")
    client.get("/ui/login")
    csrf = client.cookies.get("csrf_token")
    client.post("/ui/login", data={"tenant_slug": tenant.slug, "email": users["admin"].email, "password": TenantFactory.PASSWORD,
                                   "csrf_token": csrf, "next": "/ui/"})

    page = client.post("/ui/admin/webhooks", data={"csrf_token": csrf, "url": receiver.url, "event_types": ["exception.created"]})
    assert page.status_code == 200 and "whsec_" in page.text and page.headers["cache-control"] == "no-store"
    [endpoint] = webhook_service.list_endpoints(db_session, tenant.id)

    test = client.post(f"/ui/admin/webhooks/{endpoint.id}/test", data={"csrf_token": csrf}, follow_redirects=False)
    assert test.status_code == 303
    jobs.drain(db_session.get_bind())
    assert json.loads(receiver.requests[-1]["body"])["type"] == "webhook.test"
    detail = client.get(f"/ui/admin/webhooks/{endpoint.id}")
    assert "webhook.test" in detail.text and "Delivered" in detail.text and "whsec_" not in detail.text

    bad = client.post("/ui/admin/webhooks", data={"csrf_token": csrf, "url": receiver.url})
    assert bad.status_code == 422 and "at least one event" in bad.text


def test_deadline_approaching_is_announced_once(receiver, db_session, tenant_factory):
    from datetime import datetime, timedelta, timezone

    tenant, account, users = tenant_factory.make(slug="hooks-deadline")
    webhook_service.create_endpoint(db_session, tenant.id, url=receiver.url, event_types=["exception.deadline_approaching"])
    soon = _make_exception(db_session, tenant, account, users, "DL1", "100.00", "999.00")
    later = _make_exception(db_session, tenant, account, users, "DL2", "100.00", "999.00")
    now = datetime.now(timezone.utc)
    soon.decision_deadline, later.decision_deadline = now + timedelta(hours=2), now + timedelta(hours=30)
    db_session.commit()

    assert webhook_service.warn_deadlines_approaching(db_session, now=now) == 1
    assert webhook_service.warn_deadlines_approaching(db_session, now=now) == 0  # once
    jobs.drain(db_session.get_bind())
    [event] = [json.loads(r["body"]) for r in receiver.requests]
    assert event["type"] == "exception.deadline_approaching" and event["data"]["exception_id"] == str(soon.id)
    assert event["data"]["decision_deadline"].startswith(soon.decision_deadline.isoformat()[:16])
