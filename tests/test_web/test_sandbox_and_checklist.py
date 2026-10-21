# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Vendor sandboxes and the integration checklist (SDK_PLAN.md P4;
services/sandbox_service.py, services/conformance_service.py)."""

import re
import threading
from datetime import date, datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from sqlalchemy import select

from openclear.config import get_settings
from openclear.domain.decision import DecisionOutcome
from openclear.domain.tenant import Tenant
from openclear.domain.user import User
from openclear.services import api_client_service, conformance_service, sandbox_service, security_group_service, webhook_service
from tests.items import _decide, _make_exception

import sys  # noqa: E402
from pathlib import Path  # noqa: E402

_SDK = str(Path(__file__).resolve().parents[2] / "sdks" / "python" / "src")
if _SDK not in sys.path:
    sys.path.insert(0, _SDK)

from openclear_sdk import OpenClear, WebhookVerificationError, webhooks  # noqa: E402


def _signup(client, organization="Acme Core Systems", email="dev@acme-core.example"):
    client.get("/ui/sandbox")
    return client.post("/ui/sandbox", data={"csrf_token": client.cookies.get("csrf_token"), "organization": organization,
                                            "contact_email": email, "accept": "true"})


def test_a_vendor_gets_a_seeded_sandbox_and_it_expires(client, db_session, monkeypatch):
    monkeypatch.setattr(get_settings(), "sandbox_signup_enabled", False)
    assert client.get("/ui/sandbox").status_code == 404  # off on an ordinary installation

    monkeypatch.setattr(get_settings(), "sandbox_signup_enabled", True)
    page = _signup(client)
    assert page.status_code == 200 and page.headers["cache-control"] == "no-store"
    slug = re.search(r"/ui/login/(sbx-[0-9a-f]{8})", page.text).group(1)
    password = re.search(r'<dt>Password</dt><dd><code class="secret">([^<]+)</code>', page.text).group(1)
    client_id = re.search(r"(occ_[0-9a-f]{32}_[A-Za-z0-9_-]+)", page.text).group(1)
    secret = re.search(r'<dt>Client secret</dt><dd><code class="secret">([^<]+)</code>', page.text).group(1)
    tenant = db_session.execute(select(Tenant).where(Tenant.slug == slug)).scalar_one()
    assert tenant.is_sandbox and tenant.sandbox_contact_email == "dev@acme-core.example" and tenant.sandbox_expires_at

    client.cookies.clear()
    client.get("/ui/login")
    signed_in = client.post("/ui/login", data={"tenant_slug": slug, "email": f"admin@{slug}.sandbox.example.com", "password": password,
                                               "csrf_token": client.cookies.get("csrf_token"), "next": "/ui/"}, follow_redirects=False)
    assert signed_in.status_code == 303
    oc = OpenClear("http://testserver", client_id=client_id, client_secret=secret, http_client=client)
    assert len(oc.issued_items.list_issued_items()) > 0  # seeded

    second = _signup(client, "Another Vendor", "dev@another.example")
    assert second.status_code == 200  # no clash with the first sandbox's people

    tenant.sandbox_expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    db_session.commit()
    assert sandbox_service.expire_due(db_session) == 1
    db_session.refresh(tenant)
    assert not tenant.is_active
    assert db_session.execute(select(User).where(User.email.like(f"%@{slug}.sandbox.example.com"))).first() is None


def test_signup_needs_the_terms_and_a_real_looking_address(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "sandbox_signup_enabled", True)
    client.get("/ui/sandbox")
    no_terms = client.post("/ui/sandbox", data={"csrf_token": client.cookies.get("csrf_token"), "organization": "X", "contact_email": "a@b.example"})
    assert no_terms.status_code == 422
    assert _signup(client, email="not-an-email").status_code == 422


class _Receiver:
    """A webhook receiver that verifies signatures (or, `careless`, accepts anything)."""

    def __init__(self, secret_box: dict, careless: bool = False) -> None:
        receiver = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                body = self.rfile.read(int(self.headers["content-length"]))
                try:
                    if not careless:
                        webhooks.verify(secret_box["secret"], dict(self.headers), body)
                    self.send_response(204)
                except WebhookVerificationError:
                    self.send_response(400)
                self.end_headers()

            def log_message(self, *a):
                pass

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}/hooks"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        receiver.secret_box = secret_box


def test_the_checklist_follows_what_the_integration_does(client, db_session, tenant_factory, monkeypatch, engine):
    monkeypatch.setattr(get_settings(), "webhook_allow_private_hosts", True)
    tenant, account, users = tenant_factory.make(slug="checklist")
    status = lambda: {c.key: c.status for c in conformance_service.checklist(db_session, tenant.id)}  # noqa: E731
    assert set(status().values()) == {"todo"}

    group = security_group_service.get_security_group_by_name(db_session, tenant.id, "Admin")
    issued = api_client_service.create_client(db_session, tenant.id, name="Core", security_group_id=group.id)
    db_session.commit()
    oc = OpenClear("http://testserver", client_id=issued.client.client_id, client_secret=issued.secret, http_client=client)
    oc.customers.upsert_customer("CIF-1", {"name": "Acme"})
    oc.issued_items.create_issued_item({"account_id": str(account.id), "check_number": "1", "amount": "5.00", "payee_name": "V",
                                        "issue_date": str(date(2026, 3, 1))})
    oc.paid_items.create_paid_item({"account_id": str(account.id), "check_number": "1", "presented_amount": "5.00",
                                    "presented_date": str(date(2026, 3, 2))})
    _decide(db_session, tenant, users, _make_exception(db_session, tenant, account, users, "2", "1.00", "2.00"), DecisionOutcome.PAY)
    oc.decision_feed.consume("core", lambda d: None)

    box: dict = {}
    good = _Receiver(box)
    endpoint, secret = webhook_service.create_endpoint(db_session, tenant.id, url=good.url, event_types=["exception.created"])
    box["secret"] = secret
    webhook_service.deliver(db_session, webhook_service.send_test(db_session, tenant.id, endpoint.id).id)
    db_session.commit()
    conformance_service.probe_webhooks(db_session, tenant.id)
    db_session.commit()

    summary = conformance_service.summary(conformance_service.checklist(db_session, tenant.id))
    assert summary["certified"], summary
    assert {c["key"]: c["status"] for c in summary["checks"]}["acts_for_people"] == "todo"  # optional

    # A receiver that doesn't check signatures fails the probe.
    careless = _Receiver(box, careless=True)
    endpoint.url = careless.url
    db_session.commit()
    result = conformance_service.probe_webhooks(db_session, tenant.id)
    db_session.commit()
    assert not result.passed and "must be refused" in result.detail
    assert not conformance_service.summary(conformance_service.checklist(db_session, tenant.id))["certified"]
