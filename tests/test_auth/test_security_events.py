# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Security events (MASTER_PLAN.md M1; SIEM_PLAN.md S2 and S4; services/security_events.py):
sign-ins and their failures, lockouts, tokens, refusals, changes from the action log, the
pull feeds, and that no secret ever ends up in an event."""

import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from openclear.config import get_settings
from openclear.domain.security_event import SecurityEvent
from openclear.services import security_events
from tests.conftest import TenantFactory, login_headers

SENTINEL = "Sentinel-Secret-Value-123!"


def _events(db_session, name=None):
    db_session.expire_all()
    stmt = select(SecurityEvent).order_by(SecurityEvent.id)
    if name:
        stmt = stmt.where(SecurityEvent.name == name)
    return db_session.execute(stmt).scalars().all()


def test_sign_ins_failures_and_lockouts(client, db_session, tenant_factory, monkeypatch):
    tenant, _account, users = tenant_factory.make(slug="sec-login")
    email = users["preparer"].email
    monkeypatch.setattr(get_settings(), "login_max_failed_attempts", 3)

    def attempt(password, slug=tenant.slug, who=email):
        return client.post("/api/v1/auth/login", json={"tenant_slug": slug, "email": who, "password": password})

    assert attempt(TenantFactory.PASSWORD).status_code == 200
    [ok] = _events(db_session, "auth.login.succeeded")
    assert ok.tenant_id == tenant.id and ok.actor_label == email and ok.source_ip and ok.request_id and ok.channel == "api"

    attempt(SENTINEL)
    attempt(SENTINEL)
    attempt(SENTINEL)  # the third reaches the threshold
    failed = _events(db_session, "auth.login.failed")
    assert [e.reason for e in failed] == ["invalid_credentials"] * 3 and all(e.outcome == "failure" for e in failed)
    [lockout] = _events(db_session, "auth.lockout")
    assert lockout.tenant_id == tenant.id and lockout.actor_label == email
    attempt(TenantFactory.PASSWORD)  # still locked
    assert _events(db_session, "auth.login.failed")[-1].reason == "locked"

    attempt("x", slug="no-such-bank", who="someone@example.com")
    unknown = _events(db_session, "auth.login.failed")[-1]
    assert unknown.reason == "unknown_bank" and unknown.tenant_id is None

    # No password or token anywhere in what was recorded.
    for event in _events(db_session):
        assert SENTINEL not in json.dumps(security_events.as_record(event)) and TenantFactory.PASSWORD not in json.dumps(
            security_events.as_record(event))


def test_tokens_refusals_and_changes(client, db_session, tenant_factory):
    from openclear.services import api_client_service, security_group_service

    tenant, _account, users = tenant_factory.make(slug="sec-token")
    group = security_group_service.get_security_group_by_name(db_session, tenant.id, "Admin")
    issued = api_client_service.create_client(db_session, tenant.id, name="Core", security_group_id=group.id)
    db_session.commit()
    good = client.post("/api/v1/oauth/token", data={"grant_type": "client_credentials", "client_id": issued.client.client_id,
                                                    "client_secret": issued.secret})
    assert good.status_code == 200
    bad = {"grant_type": "client_credentials", "client_id": issued.client.client_id, "client_secret": SENTINEL}
    for _ in range(3):
        assert client.post("/api/v1/oauth/token", data=bad).status_code == 401
    assert _events(db_session, "auth.token.issued")[0].actor_label == "Core"
    assert len(_events(db_session, "auth.token.failed")) == 1  # a flood is sampled: one a minute per client and source

    # A permission refusal, and a forged token.
    viewer = login_headers(client, tenant.slug, users["approver"].email)
    assert client.get("/api/v1/users", headers=viewer).status_code == 403
    [denied] = _events(db_session, "access.denied")
    assert denied.reason == "user:manage" and denied.actor_id == str(users["approver"].id)
    assert client.get("/api/v1/accounts", headers={"Authorization": "Bearer a.b.c"}).status_code == 401
    assert _events(db_session, "auth.token.failed")[-1].reason in ("invalid_token", "invalid_signature")

    # A change already in the action log is a security event too, linked by its sequence.
    from openclear.services import audit_log_service

    second = api_client_service.create_client(db_session, tenant.id, name="Second", security_group_id=group.id)
    audit_log_service.record_action(db_session, tenant.id, actor_user_id=users["admin"].id, channel="web", action="api_client.create",
                                    summary="Created API client Second", resource_type="api_client", resource_id=second.client.id)
    db_session.commit()
    [event] = _events(db_session, "api_client.created")
    assert event.audit_sequence and event.severity == "high" and event.actor_id == str(users["admin"].id)
    assert issued.secret not in json.dumps(security_events.as_record(event))


def test_the_pull_feeds(client, db_session, tenant_factory):
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    tenant, account, users = tenant_factory.make(slug="sec-feed")
    other, _a, other_users = tenant_factory.make(slug="sec-feed-other")
    admin = login_headers(client, tenant.slug, users["admin"].email)
    login_headers(client, other.slug, other_users["admin"].email)
    client.post("/api/v1/paid-items", headers=admin, json={
        "account_id": str(account.id), "check_number": "F-1", "presented_amount": "5.00", "presented_date": "2026-03-02"})

    page = client.get("/api/v1/security-events?limit=1", headers=admin).json()
    assert len(page["events"]) == 1 and page["events"][0]["bank"]["slug"] == "sec-feed"
    rest = client.get(f"/api/v1/security-events?after={page['next_after']}", headers=admin).json()
    assert all(e["bank"]["slug"] == "sec-feed" for e in page["events"] + rest["events"])  # never another bank's
    assert client.get("/api/v1/security-events", headers=login_headers(client, tenant.slug, users["preparer"].email)).status_code == 403

    # The action log, verifiable with nothing but the public key.
    log = client.get("/api/v1/audit-log", headers=admin).json()
    key = serialization.load_pem_public_key(log["signature"]["public_key"].encode())
    previous = None
    for entry in log["entries"]:
        key.verify(bytes.fromhex(entry["entry_hash"]), entry["signed_text"].encode(), ec.ECDSA(hashes.SHA256()))
        assert entry["prev_entry_hash"] == previous
        previous = entry["entry_hash"]
    assert any(entry["action"] == "paid_item.create" for entry in log["entries"])


def test_the_operator_sees_every_bank_and_none(client, db_session, tenant_factory):
    from openclear.services import platform_api_key_service

    tenant, _account, users = tenant_factory.make(slug="sec-operator")
    client.post("/api/v1/auth/login", json={"tenant_slug": "nobody-here", "email": "x@example.com", "password": "x"})
    login_headers(client, tenant.slug, users["admin"].email)
    _row, key = platform_api_key_service.generate_and_create(db_session, "ops", scopes=("operations",))
    db_session.commit()
    events = client.get("/api/v1/platform/security-events", headers={"X-Api-Key": key}).json()["events"]
    assert {e.get("bank", {}).get("slug") for e in events} >= {None, "sec-operator"}


def test_housekeeping_clears_old_events(db_session, tenant_factory):
    from openclear.services import housekeeping_service

    tenant, _account, _users = tenant_factory.make(slug="sec-old")
    old = security_events.emit(db_session, "auth.logout", tenant_id=tenant.id)
    security_events.emit(db_session, "auth.logout", tenant_id=tenant.id)
    old.occurred_at = datetime.now(timezone.utc) - timedelta(days=40)
    db_session.commit()
    result = housekeeping_service.run(db_session)
    assert result.deleted["security_events"] == 1 and len(_events(db_session, "auth.logout")) == 1

