# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""What a bank's data pipeline needs (docs/guides/bi.md): `updated_since` on the lists, with
`customer_id` and `updated_at` in each row; read-only API clients, for the bank or scoped to
one customer; and the full data export through the API."""

import io
import uuid
import time
import zipfile
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import update

from openclear.domain.account import Account
from openclear.domain.issued_item import IssuedItem
from openclear.services import account_service, api_client_service, customer_service, issued_item_service, security_group_service


_PIPELINE = ["account:read", "issued_item:read", "paid_item:read", "stop_payment:read", "exception:read", "ach_authorization:read",
             "ach_transaction:read", "customer:manage", "data_export:run", "issued_item:write", "api_client:manage"]


def _group(db_session, tenant):
    """Reads, the full export, and (to show read-only drops them) some writes."""
    existing = security_group_service.get_security_group_by_name(db_session, tenant.id, "Pipeline")
    return existing or security_group_service.create_security_group(
        db_session, tenant.id, security_group_service.SecurityGroupInput(name="Pipeline", permissions=_PIPELINE))


def _client(db_session, tenant, *, read_only=False, customer_id=None, act=False):
    group_row = _group(db_session, tenant)
    issued = api_client_service.create_client(db_session, tenant.id, name=f"Pipeline {time.monotonic_ns()}", security_group_id=group_row.id,
                                              customer_id=customer_id, read_only=read_only, can_act_for_users=act)
    db_session.commit()
    return issued


def _headers(client, issued) -> dict:
    token = client.post("/api/v1/oauth/token", data={"grant_type": "client_credentials", "client_id": issued.client.client_id,
                                                     "client_secret": issued.secret}).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def _item(db_session, tenant, account, number, *, customer=None):
    item = issued_item_service.create_issued_item(db_session, tenant.id, issued_item_service.IssuedItemInput(
        account_id=account.id, check_number=number, amount=Decimal("10.00"), payee_name="Payee", issue_date=date(2026, 1, 1)),
        submitted_by_user_id=None)
    db_session.commit()
    return item


def test_updated_since_returns_only_what_changed_in_the_order_it_changed(client, db_session, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="bi-updated-since")
    old, changed, new = (_item(db_session, tenant, account, n) for n in ("1001", "1002", "1003"))
    long_ago = datetime.now(timezone.utc) - timedelta(days=3)
    db_session.execute(update(IssuedItem).where(IssuedItem.id.in_([old.id, changed.id])).values(updated_at=long_ago))
    # SQLite's clock has one-second steps: put the new item clearly before the void to come.
    db_session.execute(update(IssuedItem).where(IssuedItem.id == new.id).values(updated_at=datetime.now(timezone.utc) - timedelta(hours=2)))
    db_session.commit()
    headers = _headers(client, _client(db_session, tenant, read_only=True))
    since = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()

    assert [r["check_number"] for r in client.get("/api/v1/issued-items", params={"updated_since": since}, headers=headers).json()] == ["1003"]
    void = client.patch(f"/api/v1/issued-items/{changed.id}/void", json={"reason": "lost"},
                        headers=_headers(client, _client(db_session, tenant)))
    assert void.status_code == 200
    rows = client.get("/api/v1/issued-items", params={"updated_since": since}, headers=headers)
    assert [r["check_number"] for r in rows.json()] == ["1003", "1002"]  # oldest change first; the void is the newest
    assert rows.headers["X-Total-Count"] == "2"
    assert rows.json()[0]["updated_at"] and "customer_id" in rows.json()[0]
    assert len(client.get("/api/v1/issued-items", headers=headers).json()) == 3  # without it, everything, as before


@pytest.mark.parametrize("path", ["/api/v1/accounts", "/api/v1/customers", "/api/v1/exceptions", "/api/v1/paid-items",
                                  "/api/v1/stop-payments", "/api/v1/ach/transactions", "/api/v1/ach/authorizations"])
def test_every_list_takes_updated_since(client, db_session, tenant_factory, path):
    tenant, _account, _users = tenant_factory.make(slug=f"bi-lists-{path.rsplit('/', 1)[1]}")
    headers = _headers(client, _client(db_session, tenant))
    future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    resp = client.get(path, params={"updated_since": future}, headers=headers)
    assert resp.status_code == 200 and resp.json() == [] and resp.headers["X-Total-Count"] == "0"
    past = client.get(path, params={"updated_since": "2000-01-01T00:00:00Z"}, headers=headers)
    assert past.status_code == 200
    for row in past.json():
        assert "updated_at" in row


def test_changes_through_the_orm_move_updated_at(db_session, tenant_factory):
    tenant, account, _users = tenant_factory.make(slug="bi-onupdate")
    long_ago = datetime.now(timezone.utc) - timedelta(days=3)
    db_session.execute(update(Account).where(Account.id == account.id).values(updated_at=long_ago))
    db_session.commit()
    db_session.refresh(account)
    account.name = "Renamed"
    db_session.commit()
    db_session.refresh(account)
    stamp = account.updated_at if account.updated_at.tzinfo else account.updated_at.replace(tzinfo=timezone.utc)
    assert stamp > long_ago + timedelta(days=2)


def test_a_read_only_client_reads_and_exports_but_never_writes(client, db_session, tenant_factory):
    tenant, account, _users = tenant_factory.make(slug="bi-read-only")
    headers = _headers(client, _client(db_session, tenant, read_only=True))
    assert client.get("/api/v1/issued-items", headers=headers).status_code == 200
    write = client.post("/api/v1/issued-items", headers=headers, json={
        "account_id": str(account.id), "check_number": "9", "amount": "1.00", "payee_name": "X", "issue_date": "2026-01-01"})
    assert write.status_code == 403
    assert client.post(f"/api/v1/exceptions/{account.id}/decide", headers=headers, json={"outcome": "pay"}).status_code in (403, 404)
    assert client.get("/api/v1/data-exports", headers=headers).status_code == 200
    listed = client.get("/api/v1/api-clients", headers=_headers(client, _client(db_session, tenant))).json()
    assert any(c["read_only"] for c in listed)


def test_a_customer_scoped_read_only_client_sees_only_its_customer(client, db_session, tenant_factory):
    tenant, account, _users = tenant_factory.make(slug="bi-customer-feed")
    customer = customer_service.create_customer(db_session, tenant.id, customer_service.CustomerInput(customer_number="F-1", name="Feed Co"))
    theirs = account_service.create_account(db_session, tenant.id, account_service.AccountInput(account_number="555000111", name="Feed op",
                                                                                                 customer_id=customer.id))
    db_session.commit()
    _item(db_session, tenant, account, "2001")  # the bank's own account
    _item(db_session, tenant, theirs, "2002")
    headers = _headers(client, _client(db_session, tenant, read_only=True, customer_id=customer.id))
    rows = client.get("/api/v1/issued-items", headers=headers).json()
    assert [r["check_number"] for r in rows] == ["2002"] and rows[0]["customer_id"] == str(customer.id)


def test_the_full_data_export_through_the_api(client, db_session, tenant_factory):
    tenant, account, _users = tenant_factory.make(slug="bi-export")
    customer = customer_service.create_customer(db_session, tenant.id, customer_service.CustomerInput(customer_number="E-1", name="Export Co"))
    db_session.commit()
    _item(db_session, tenant, account, "3001")
    bank = _headers(client, _client(db_session, tenant, read_only=True))

    started = client.post("/api/v1/data-exports", headers=bank)
    assert started.status_code == 202
    export_id = started.json()["id"]
    from openclear.workers import jobs

    jobs.drain(db_session.get_bind())
    status = client.get(f"/api/v1/data-exports/{export_id}", headers=bank).json()
    assert status["status"] == "completed" and status["file_url"] == f"/api/v1/data-exports/{export_id}/file"
    archive = client.get(status["file_url"], headers=bank)
    assert archive.status_code == 200 and archive.headers["content-type"] == "application/zip"
    names = zipfile.ZipFile(io.BytesIO(archive.content)).namelist()
    assert any(n.startswith("data/") and n.endswith(".json") for n in names)

    one = client.post("/api/v1/data-exports", params={"customer_number": "E-1"}, headers=bank)
    assert one.status_code == 202 and one.json()["customer_id"] == str(customer.id)
    assert client.post("/api/v1/data-exports", params={"customer_number": "NOPE"}, headers=bank).status_code == 404

    # A customer's client exports its own customer only, and can't see the bank's exports.
    theirs = _headers(client, _client(db_session, tenant, read_only=True, customer_id=customer.id))
    own = client.post("/api/v1/data-exports", params={"customer_number": "anything"}, headers=theirs)
    assert own.status_code == 202 and own.json()["customer_id"] == str(customer.id)
    assert client.get(f"/api/v1/data-exports/{export_id}", headers=theirs).status_code == 404
    assert {e["customer_id"] for e in client.get("/api/v1/data-exports", headers=theirs).json()} == {str(customer.id)}


def test_paging_changes_never_misses_a_row_that_changes_meanwhile(client, db_session, tenant_factory):
    """Seven rows changed in the same instant (as a bulk import does), read three at a time by
    following the next link; one row already read changes in the middle. Every row arrives,
    the changed one again at the end."""
    tenant, account, _users = tenant_factory.make(slug="bi-keyset")
    items = [_item(db_session, tenant, account, f"40{n}") for n in range(7)]
    same = datetime.now(timezone.utc) - timedelta(hours=1)
    db_session.execute(update(IssuedItem).where(IssuedItem.id.in_([i.id for i in items])).values(updated_at=same))
    db_session.commit()
    headers = _headers(client, _client(db_session, tenant, read_only=True))
    url = f"/api/v1/issued-items?updated_since={(same - timedelta(seconds=1)).isoformat().replace('+', '%2B')}&limit=3"
    seen, pages = [], 0
    while url:
        page = client.get(url, headers=headers)
        assert page.status_code == 200
        seen += [r["id"] for r in page.json()]
        pages += 1
        if pages == 1:  # a row already read changes while the pipeline is mid-way
            db_session.execute(update(IssuedItem).where(IssuedItem.id == uuid.UUID(page.json()[0]["id"])).values(updated_at=datetime.now(timezone.utc)))
            db_session.commit()
        link = page.headers.get("Link")
        url = link[1:link.index(">")].replace("http://testserver", "") if link else None
        assert pages < 10
    assert set(seen) == {str(i.id) for i in items}
    assert seen.count(seen[0]) == 2  # the changed row came again, after the others
