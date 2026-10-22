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
from tests.items import _make_customer_with_account
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


def _customer_attester(db_session, tenant_factory, slug: str):
    """A customer's user who may attest (`compliance:attest` isn't masked for customer sessions)."""
    tenant, _account, _users = tenant_factory.make(slug=slug)
    customer, _account = _make_customer_with_account(db_session, tenant, "CS0-1")
    admin_group = security_group_service.get_security_group_by_name(db_session, tenant.id, "Admin")
    attester = user_service.create_user_with_membership(db_session, tenant.id, email=f"cfo@{slug}-customer.example.com",
                                                        password=TenantFactory.PASSWORD, security_group_id=admin_group.id,
                                                        customer_id=customer.id)
    db_session.commit()
    return tenant, attester


@pytest.mark.asvs("5.3.2")
def test_a_customers_user_cant_write_outside_the_folder(client, db_session, tenant_factory, archives):
    tmp_path, area = archives
    tenant, attester = _customer_attester(db_session, tenant_factory, "cs0-web")
    csrf = _login(client, tenant.slug, attester.email)

    client.post("/ui/compliance/cuecs/C-AC-2-01/attestations",
                data={"csrf_token": csrf, "start": "2026-01-01", "end": "2026-03-31", "result": "operated", "statement": "Fine."},
                files={"evidence_file": (EVIL, b"payload", "text/plain")})

    _nothing_escaped(tmp_path, area)
    assert [p.name.split("-", 1)[1] for p in area.rglob("*") if p.is_file()] == ["escaped.txt"]


@pytest.mark.asvs("5.3.2")
def test_through_the_api_too(client, db_session, tenant_factory, archives):
    tmp_path, area = archives
    tenant, attester = _customer_attester(db_session, tenant_factory, "cs0-api")
    headers = login_headers(client, tenant.slug, attester.email)
    made = client.post("/api/v1/compliance/cuecs/C-AC-2-01/attestations", headers=headers,
                       data={"start": "2026-01-01", "end": "2026-03-31", "result": "operated", "statement": "Reviewed."},
                       files={"evidence_file": (EVIL, b"payload", "text/plain")})
    assert made.status_code == 201, made.text
    assert made.json()["files"][0]["name"] == "escaped.txt"
    _nothing_escaped(tmp_path, area)


@pytest.fixture
def every_area(tmp_path, monkeypatch):
    """Each storage area in its own folder under tmp_path."""
    for area, setting in storage.AREAS.items():
        monkeypatch.setattr(get_settings(), setting, str(tmp_path / "areas" / area))
    return tmp_path


@pytest.mark.parametrize("area", sorted(storage.AREAS))
def test_every_area_refuses_a_key_that_climbs_out(every_area, area):
    with pytest.raises(storage.UnsafeKey):
        storage.LocalStorage().put(area, "t/../../../escaped.txt", b"x")
    assert list(every_area.rglob("escaped.txt")) == []
    stored = storage.LocalStorage().put(area, "t/fine.txt", b"x")
    assert Path(stored).resolve().is_relative_to((every_area / "areas" / area).resolve())


@pytest.mark.parametrize("area", sorted(storage.AREAS))
def test_a_link_inside_an_area_cant_lead_out_of_it(every_area, area):
    """A key safe_key accepts, through a symlink that points outside: only LocalStorage's own
    check of the resolved path catches it (CODE_SCANNING_PLAN.md CS-D1, "the real control")."""
    root = every_area / "areas" / area
    outside = every_area / "outside"
    (root / "t").mkdir(parents=True)
    outside.mkdir()
    (root / "t" / "link").symlink_to(outside, target_is_directory=True)
    assert storage.safe_key("t/link/escaped.txt") == "t/link/escaped.txt"
    with pytest.raises(storage.UnsafeKey):
        storage.LocalStorage().put(area, "t/link/escaped.txt", b"x")
    assert list(outside.iterdir()) == []
