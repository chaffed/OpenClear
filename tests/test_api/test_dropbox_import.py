# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import os
import time
from pathlib import Path

from openclear.config import get_settings
from openclear.services import account_service, customer_service, security_group_service, user_service
from tests.conftest import TenantFactory, login_headers

_OLD_ENOUGH = 60


def _configure_dropbox(monkeypatch, tmp_path) -> Path:
    root = tmp_path / "dropbox"
    monkeypatch.setenv("OPENCLEAR_AUTO_IMPORT_DROPBOX_DIR", str(root))
    get_settings.cache_clear()
    return root


def _drop(root: Path, tenant_slug: str, kind_subpath: str, filename: str, content: bytes) -> Path:
    inbox = root / tenant_slug / kind_subpath / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    path = inbox / filename
    path.write_bytes(content)
    old_time = time.time() - _OLD_ENOUGH
    os.utime(path, (old_time, old_time))
    return path


def _issued_items_csv(account_number: str, check_number: str) -> bytes:
    return (
        "account_number,check_number,amount,payee_name,issue_date\n"
        f"{account_number},{check_number},150.00,Vendor,2026-01-01\n"
    ).encode()


def test_run_import_scoped_to_calling_tenant_only(client, db_session, tenant_factory, monkeypatch, tmp_path):
    root = _configure_dropbox(monkeypatch, tmp_path)
    tenant_a, account_a, users_a = tenant_factory.make(slug="api-import-tenant-a")
    tenant_b, account_b, _users_b = tenant_factory.make(slug="api-import-tenant-b")
    headers = login_headers(client, tenant_a.slug, users_a["preparer"].email)

    _drop(root, tenant_a.slug, "issued_items", "a.csv", _issued_items_csv(account_a.account_number, "9001"))
    _drop(root, tenant_b.slug, "issued_items", "b.csv", _issued_items_csv(account_b.account_number, "9002"))

    resp = client.post("/api/v1/import/run", headers=headers)

    assert resp.status_code == 200
    body = resp.json()
    assert len(body["results"]) == 1
    assert body["results"][0]["tenant_slug"] == tenant_a.slug
    assert body["results"][0]["filename"] == "a.csv"
    # tenant_b's file is untouched by tenant_a's on-demand trigger
    assert (root / tenant_b.slug / "issued_items" / "inbox" / "b.csv").exists()


def test_run_import_scoped_to_calling_customer_only(client, db_session, tenant_factory, monkeypatch, tmp_path):
    root = _configure_dropbox(monkeypatch, tmp_path)
    tenant, _house_account, _users = tenant_factory.make(slug="api-import-customer-scope")
    customer_a = customer_service.create_customer(
        db_session, tenant.id, customer_service.CustomerInput(customer_number="C-A", name="Customer A")
    )
    customer_b = customer_service.create_customer(
        db_session, tenant.id, customer_service.CustomerInput(customer_number="C-B", name="Customer B")
    )
    account_a = account_service.create_account(
        db_session, tenant.id, account_service.AccountInput(account_number="A-1", name="A Account", customer_id=customer_a.id)
    )
    account_b = account_service.create_account(
        db_session, tenant.id, account_service.AccountInput(account_number="B-1", name="B Account", customer_id=customer_b.id)
    )
    preparer_group = security_group_service.get_security_group_by_name(db_session, tenant.id, "Preparer")
    user_a = user_service.create_user_with_membership(
        db_session, tenant.id, email="staff-a@api-import-customer-scope.example.com", password=TenantFactory.PASSWORD,
        security_group_id=preparer_group.id, customer_id=customer_a.id,
    )
    db_session.commit()
    headers_a = login_headers(client, tenant.slug, user_a.email)

    _drop(root, tenant.slug, "C-A/issued_items", "a.csv", _issued_items_csv(account_a.account_number, "1001"))
    _drop(root, tenant.slug, "C-B/issued_items", "b.csv", _issued_items_csv(account_b.account_number, "2001"))
    _drop(root, tenant.slug, "issued_items", "tenant-wide.csv", _issued_items_csv("A-1", "3001"))

    resp = client.post("/api/v1/import/run", headers=headers_a)

    assert resp.status_code == 200
    body = resp.json()
    assert len(body["results"]) == 1
    assert body["results"][0]["filename"] == "a.csv"
    assert (root / tenant.slug / "C-B" / "issued_items" / "inbox" / "b.csv").exists()
    assert (root / tenant.slug / "issued_items" / "inbox" / "tenant-wide.csv").exists()


def test_viewer_cannot_trigger_import(client, tenant_factory, monkeypatch, tmp_path):
    _configure_dropbox(monkeypatch, tmp_path)
    tenant, _account, users = tenant_factory.make(slug="api-import-viewer-forbidden")
    headers = login_headers(client, tenant.slug, users["viewer"].email)

    resp = client.post("/api/v1/import/run", headers=headers)
    assert resp.status_code == 403
