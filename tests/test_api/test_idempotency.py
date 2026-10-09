# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Idempotency-Key on API writes (SDK_PLAN.md P0; api/idempotency.py)."""

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from openclear.domain.idempotency_record import IdempotencyRecord
from openclear.domain.issued_item import IssuedItem
from openclear.services import housekeeping_service
from tests.conftest import login_headers


def _item(account, number="9001", amount="12.00"):
    return {"account_id": str(account.id), "check_number": number, "amount": amount, "payee_name": "Vendor", "issue_date": "2026-03-01"}


def _issued(db_session, tenant) -> int:
    return db_session.scalar(select(func.count()).select_from(IssuedItem).where(IssuedItem.tenant_id == tenant.id))


def test_a_retry_with_the_same_key_replays_instead_of_repeating(client, db_session, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="idem-replay")
    headers = {**login_headers(client, tenant.slug, users["admin"].email), "Idempotency-Key": "req-1"}

    first = client.post("/api/v1/issued-items", headers=headers, json=_item(account))
    retry = client.post("/api/v1/issued-items", headers=headers, json=_item(account))

    assert first.status_code == retry.status_code == 201
    assert retry.json() == first.json() and retry.headers["idempotency-replayed"] == "true"
    assert "idempotency-replayed" not in first.headers
    assert _issued(db_session, tenant) == 1


def test_the_same_key_with_a_different_request_is_refused(client, db_session, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="idem-mismatch")
    headers = {**login_headers(client, tenant.slug, users["admin"].email), "Idempotency-Key": "req-2"}
    assert client.post("/api/v1/issued-items", headers=headers, json=_item(account)).status_code == 201
    resp = client.post("/api/v1/issued-items", headers=headers, json=_item(account, amount="99.00"))
    assert resp.status_code == 422 and "different request" in resp.json()["detail"]
    assert _issued(db_session, tenant) == 1


def test_keys_are_per_caller_and_requests_without_one_are_unaffected(client, db_session, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="idem-callers")
    admin = login_headers(client, tenant.slug, users["admin"].email)
    preparer = login_headers(client, tenant.slug, users["preparer"].email)
    assert client.post("/api/v1/issued-items", headers={**admin, "Idempotency-Key": "same"}, json=_item(account, "1")).status_code == 201
    assert client.post("/api/v1/issued-items", headers={**preparer, "Idempotency-Key": "same"}, json=_item(account, "2")).status_code == 201
    # Without the header nothing is stored: two calls are two items.
    assert client.post("/api/v1/issued-items", headers=admin, json=_item(account, "3")).status_code == 201
    assert client.post("/api/v1/issued-items", headers=admin, json=_item(account, "4")).status_code == 201
    assert _issued(db_session, tenant) == 4
    assert db_session.scalar(select(func.count()).select_from(IdempotencyRecord)) == 2


def test_an_error_response_is_replayed_too_but_a_server_error_is_not(client, db_session, tenant_factory, monkeypatch):
    tenant, account, users = tenant_factory.make(slug="idem-errors")
    headers = login_headers(client, tenant.slug, users["admin"].email)
    bad = {**_item(account), "account_id": "00000000-0000-0000-0000-000000000000"}
    first = client.post("/api/v1/issued-items", headers={**headers, "Idempotency-Key": "bad-1"}, json=bad)
    again = client.post("/api/v1/issued-items", headers={**headers, "Idempotency-Key": "bad-1"}, json=bad)
    assert first.status_code == again.status_code and first.status_code < 500 and again.headers.get("idempotency-replayed") == "true"

    from openclear.services import issued_item_service

    def boom(*args, **kwargs):
        raise RuntimeError("database fell over")

    monkeypatch.setattr(issued_item_service, "create_issued_item", boom)
    from fastapi.testclient import TestClient

    with TestClient(client.app, raise_server_exceptions=False) as quiet:
        assert quiet.post("/api/v1/issued-items", headers={**headers, "Idempotency-Key": "boom"}, json=_item(account, "77")).status_code == 500
    assert db_session.scalar(select(func.count()).select_from(IdempotencyRecord).where(IdempotencyRecord.key == "boom")) == 0
    monkeypatch.undo()
    assert client.post("/api/v1/issued-items", headers={**headers, "Idempotency-Key": "boom"}, json=_item(account, "77")).status_code == 201


def test_a_key_still_in_flight_answers_409(client, db_session, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="idem-inflight")
    headers = login_headers(client, tenant.slug, users["admin"].email)
    from openclear.auth.security import decode_token

    user_id = decode_token(headers["Authorization"].split()[1])["sub"]
    import hashlib
    import json
    import uuid

    body = json.dumps(_item(account)).encode()
    db_session.add(IdempotencyRecord(
        tenant_id=tenant.id, user_id=uuid.UUID(user_id), key="busy",
        request_hash=hashlib.sha256(b"\n".join([b"POST", b"/api/v1/issued-items", b"", body])).hexdigest(),
    ))
    db_session.commit()
    resp = client.post("/api/v1/issued-items", headers={**headers, "Idempotency-Key": "busy", "Content-Type": "application/json"}, content=body)
    assert resp.status_code == 409


def test_a_bad_key_is_rejected_and_old_keys_are_cleaned_up(client, db_session, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="idem-cleanup")
    headers = login_headers(client, tenant.slug, users["admin"].email)
    assert client.post("/api/v1/issued-items", headers={**headers, "Idempotency-Key": "x" * 300}, json=_item(account)).status_code == 400

    assert client.post("/api/v1/issued-items", headers={**headers, "Idempotency-Key": "old"}, json=_item(account)).status_code == 201
    record = db_session.execute(select(IdempotencyRecord).where(IdempotencyRecord.key == "old")).scalar_one()
    record.created_at = datetime.now(timezone.utc) - timedelta(hours=25)
    db_session.commit()
    assert housekeeping_service.run(db_session).deleted["idempotency_keys"] == 1
