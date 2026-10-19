# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed
"""Evidence files keep to the storage folder (CODE_SCANNING_PLAN.md CS0): an uploaded file's
name once became part of its storage path unchecked, so `../` in it wrote outside the folder,
reachable by a customer's user who may attest. Now storage refuses any key that isn't a plain
relative path, and the name keeps only its last part."""

from pathlib import Path

import pytest

from openclear import storage
from openclear.config import get_settings
from openclear.services import security_group_service, user_service
from tests.conftest import TenantFactory, login_headers
from tests.test_ml.test_per_customer_ml import _make_customer_with_account
from tests.test_web.test_web_bulk_ach import _login

EVIL = "x/../../../../../../escaped.txt"


@pytest.fixture
def archives(tmp_path, monkeypatch):
    area = tmp_path / "deep" / "er" / "audit_archives"
    monkeypatch.setattr(get_settings(), "audit_archive_storage_dir", str(area))
    return tmp_path, area


def _nothing_escaped(tmp_path: Path, area: Path) -> None:
    escaped = [p for p in tmp_path.rglob("escaped.txt") if not p.resolve().is_relative_to(area.resolve())]
    assert escaped == []


@pytest.mark.parametrize("key", ["../x", "a/../../x", "/etc/x", "a//b", "a/./b", "a\\..\\b", "a\nb", ""])
def test_storage_refuses_keys_that_arent_plain_paths(key):
    with pytest.raises(storage.UnsafeKey):
        storage.put("audit_archives", key, b"x")


def test_safe_name_keeps_the_last_part():
    assert storage.safe_name(EVIL) == "escaped.txt"
    assert storage.safe_name("..\\..\\boot.ini") == "boot.ini"
    assert storage.safe_name("..") == "file" and storage.safe_name(None, "evidence") == "evidence"


@pytest.mark.asvs("5.3.2")
def test_a_customers_user_cant_write_outside_the_folder(client, db_session, tenant_factory, archives):
    tmp_path, area = archives
    tenant, _account, _users = tenant_factory.make(slug="cs0-web")
    customer, _account = _make_customer_with_account(db_session, tenant, "CS0-1")
    admin_group = security_group_service.get_security_group_by_name(db_session, tenant.id, "Admin")
    attester = user_service.create_user_with_membership(db_session, tenant.id, email="cfo@cs0-customer.example.com",
                                                        password=TenantFactory.PASSWORD, security_group_id=admin_group.id,
                                                        customer_id=customer.id)
    db_session.commit()
    csrf = _login(client, tenant.slug, attester.email)

    client.post("/ui/compliance/cuecs/C-AC-2-01/attestations",
                data={"csrf_token": csrf, "start": "2026-01-01", "end": "2026-03-31", "result": "operated", "statement": "Fine."},
                files={"evidence_file": (EVIL, b"payload", "text/plain")})

    _nothing_escaped(tmp_path, area)
    assert [p.name.split("-", 1)[1] for p in area.rglob("*") if p.is_file()] == ["escaped.txt"]


def test_through_the_api_too(client, db_session, tenant_factory, archives):
    tmp_path, area = archives
    tenant, _account, users = tenant_factory.make(slug="cs0-api")
    headers = login_headers(client, tenant.slug, users["admin"].email)
    made = client.post("/api/v1/compliance/cuecs/B-AT-3-01/attestations", headers=headers,
                       data={"start": "2026-01-01", "end": "2026-03-31", "result": "operated", "statement": "Reviewed."},
                       files={"evidence_file": (EVIL, b"payload", "text/plain")})
    assert made.status_code == 201, made.text
    assert made.json()["files"][0]["name"] == "escaped.txt"
    _nothing_escaped(tmp_path, area)
