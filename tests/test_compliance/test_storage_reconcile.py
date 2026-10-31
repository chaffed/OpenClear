# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Rows and files checked against each other (CONTINUITY_PLAN.md BC-D3): a row whose file is gone
is reported and warned about; a file no row names is reported; the platform page runs it."""

from openclear import storage
from openclear.domain.check_image import CheckImage
from openclear.services import storage_reconcile
from tests.test_web.test_staff_and_operator import _platform_login, _staff


def test_missing_files_and_orphans_are_found(db_session, tenant_factory):
    tenant, account, _users = tenant_factory.make(slug="recon")
    good = storage.put("check_images", f"{tenant.id}/good.png", b"png")
    db_session.add(CheckImage(tenant_id=tenant.id, front_image_path=good))
    gone_ref = storage.ref("check_images", f"{tenant.id}/gone.png")
    db_session.add(CheckImage(tenant_id=tenant.id, front_image_path=gone_ref))
    storage.put("bulk_uploads", f"{tenant.id}/nobody-names-me.csv", b"x")
    db_session.commit()

    found = storage_reconcile.reconcile(db_session)
    assert found.rows_checked >= 2 and not found.ok
    assert [m["ref"] for m in found.missing] == [gone_ref] and found.missing[0]["table"] == "check_image"
    assert found.orphans["bulk_uploads"] == [f"{tenant.id}/nobody-names-me.csv"] and "check_images" not in found.orphans
    assert storage_reconcile.consistency_check(db_session).status == "WARN"

    storage.put("check_images", f"{tenant.id}/gone.png", b"back")
    assert storage_reconcile.reconcile(db_session).missing == []
    assert storage_reconcile.consistency_check(db_session).status == "PASS"  # orphans are reported, not warned about


def test_the_platform_page_runs_it(client, db_session, tenant_factory):
    tenant_factory.make(slug="recon-page")
    operator = _staff(db_session, "ops@recon.example", "operator")
    _platform_login(client, operator.email)
    page = client.get("/ui/platform/continuity")
    assert page.status_code == 200 and "No backup manifest" in page.text and "No drill recorded yet" in page.text
    csrf = client.cookies.get("csrf_token")
    ran = client.post("/ui/platform/continuity/reconcile", data={"csrf_token": csrf})
    assert ran.status_code == 200 and "references checked" in ran.text
