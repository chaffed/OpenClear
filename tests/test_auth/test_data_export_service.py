# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import json
import uuid
import zipfile
from datetime import date
from decimal import Decimal

import pytest

from openclear.auth.permissions import PERMISSION_CATALOG
from openclear.bulk_import.file_storage import save_uploaded_file
from openclear.config import get_settings
from openclear.domain.bulk_upload_file import BulkUploadFile, BulkUploadKind
from openclear.domain.data_export_job import DataExportJobStatus
from openclear.networks.check.ingestion import PaidItemSubmission, ingest_paid_item
from openclear.networks.check.ocr_processing import create_check_image
from openclear.services import (
    account_service,
    customer_service,
    data_export_service,
    issued_item_service,
    security_group_service,
    tenant_service,
)


def test_data_export_permission_is_not_in_default_admin_group(db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="export-admin-default")
    admin_group = security_group_service.get_security_group_by_name(db_session, tenant.id, "Admin")

    assert "data_export:run" in PERMISSION_CATALOG
    assert "data_export:run" not in admin_group.permissions


def _run_job_sync(db_session, job):
    """run_export_job normally opens its own Session from an engine (for the real
    background-task path) — for these tests we just reuse the existing db_session's bind
    directly, avoiding a second, separate SQLite connection to the same in-memory db."""
    data_export_service.run_export_job(db_session.get_bind(), job.id, job.tenant_id)


def _read_json(zf: zipfile.ZipFile, name: str):
    return json.loads(zf.read(name))


def test_tenant_wide_export_includes_expected_data_files(db_session, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="export-tenant-wide")
    issued_item_service.create_issued_item(
        db_session, tenant.id,
        issued_item_service.IssuedItemInput(account_id=account.id, check_number="1", amount=Decimal("10.00"), payee_name="X", issue_date=date(2026, 1, 1)),
        submitted_by_user_id=users["preparer"].id,
    )
    db_session.commit()

    job = data_export_service.request_export(db_session, tenant.id, users["admin"].id)
    db_session.commit()
    _run_job_sync(db_session, job)

    db_session.expire_all()
    job = db_session.get(type(job), job.id)
    assert job.status == DataExportJobStatus.COMPLETED
    assert job.archive_path is not None
    assert job.size_bytes and job.size_bytes > 0

    with zipfile.ZipFile(job.archive_path) as zf:
        names = zf.namelist()
        assert "data/issued_items.json" in names
        assert "data/tenant.json" in names
        assert "data/customers.json" in names
        assert "data/audit_log.json" in names
        issued_items = _read_json(zf, "data/issued_items.json")
        assert len(issued_items) == 1
        assert issued_items[0]["check_number"] == "1"


def test_export_never_includes_secret_columns(db_session, tenant_factory):
    from openclear.domain.sso_connection import SsoProvider
    from openclear.services import sso_service
    from openclear.services.sso_service import SsoConnectionInput

    tenant, _account, users = tenant_factory.make(slug="export-no-secrets")
    connection = sso_service.create_connection(
        db_session, tenant.id,
        SsoConnectionInput(
            provider=SsoProvider.OKTA, display_name="Okta", issuer="https://idp.example.com", client_id="cid",
            client_secret="hunter2-secret", groups_claim_name="groups", auto_provision=False, customer_id=None,
        ),
    )
    db_session.commit()

    job = data_export_service.request_export(db_session, tenant.id, users["admin"].id)
    db_session.commit()
    _run_job_sync(db_session, job)
    db_session.expire_all()
    job = db_session.get(type(job), job.id)

    with zipfile.ZipFile(job.archive_path) as zf:
        raw_text = zf.read("data/sso_connections.json").decode("utf-8")
        assert "hunter2-secret" not in raw_text
        assert "client_secret_encrypted" not in raw_text

        users_text = zf.read("data/users.json").decode("utf-8")
        assert "hashed_password" not in users_text
    assert connection.id  # keep reference alive for readability


def test_export_includes_check_image_and_bulk_upload_files(db_session, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="export-files")
    issued_item_service.create_issued_item(
        db_session, tenant.id,
        issued_item_service.IssuedItemInput(account_id=account.id, check_number="1", amount=Decimal("10.00"), payee_name="X", issue_date=date(2026, 1, 1)),
        submitted_by_user_id=users["preparer"].id,
    )
    db_session.commit()
    paid_item = ingest_paid_item(db_session, tenant.id, PaidItemSubmission(account_id=account.id, check_number="1", presented_amount=Decimal("10.00"), presented_date=date(2026, 1, 10)))
    db_session.commit()
    check_image = create_check_image(db_session, tenant.id, front_bytes=b"front-image-bytes", back_bytes=b"back-image-bytes", paid_item_id=paid_item.id)
    db_session.commit()

    upload_id = uuid.uuid4()
    storage_path = save_uploaded_file(tenant.id, upload_id, "rows.csv", b"email,security_group\na@b.com,Viewer\n")
    upload = BulkUploadFile(
        id=upload_id, tenant_id=tenant.id, kind=BulkUploadKind.USERS, original_filename="rows.csv", content_type="text/csv",
        storage_path=storage_path, size_bytes=10, sha256_hex="0" * 64, signature_hex="0" * 64, uploaded_by_user_id=users["admin"].id,
    )
    db_session.add(upload)
    db_session.commit()

    job = data_export_service.request_export(db_session, tenant.id, users["admin"].id)
    db_session.commit()
    _run_job_sync(db_session, job)
    db_session.expire_all()
    job = db_session.get(type(job), job.id)

    with zipfile.ZipFile(job.archive_path) as zf:
        names = zf.namelist()
        check_image_files = [n for n in names if n.startswith(f"files/check_images/{check_image.id}")]
        assert len(check_image_files) == 2
        assert zf.read(f"files/check_images/{check_image.id}_front.png") == b"front-image-bytes"
        bulk_files = [n for n in names if n.startswith("files/bulk_uploads/")]
        assert len(bulk_files) == 1


def test_customer_scoped_export_includes_orphaned_check_image_and_excludes_other_customers(db_session, tenant_factory):
    """Before check_image had its own customer_id, a customer-scoped export filtered
    check images by joining through paid_item_id — which silently missed any image not
    yet linked to a paid item. Now it's a direct filter, so an orphaned image (no
    paid_item_id) still correctly shows up for its own customer, and never for another."""
    tenant, _account, users = tenant_factory.make(slug="export-orphan-check-image")
    customer_a = customer_service.create_customer(db_session, tenant.id, customer_service.CustomerInput(customer_number="C-A", name="A Co"))
    customer_b = customer_service.create_customer(db_session, tenant.id, customer_service.CustomerInput(customer_number="C-B", name="B Co"))
    orphan_image = create_check_image(
        db_session, tenant.id, front_bytes=b"orphan-front-bytes", back_bytes=None, paid_item_id=None, customer_id=customer_a.id
    )
    db_session.commit()

    job_a = data_export_service.request_export(db_session, tenant.id, users["admin"].id, customer_id=customer_a.id)
    db_session.commit()
    _run_job_sync(db_session, job_a)
    db_session.expire_all()
    job_a = db_session.get(type(job_a), job_a.id)

    with zipfile.ZipFile(job_a.archive_path) as zf:
        check_images = _read_json(zf, "data/check_images.json")
        assert [c["id"] for c in check_images] == [str(orphan_image.id)]

    job_b = data_export_service.request_export(db_session, tenant.id, users["admin"].id, customer_id=customer_b.id)
    db_session.commit()
    _run_job_sync(db_session, job_b)
    db_session.expire_all()
    job_b = db_session.get(type(job_b), job_b.id)

    with zipfile.ZipFile(job_b.archive_path) as zf:
        assert _read_json(zf, "data/check_images.json") == []


def test_customer_scoped_export_excludes_other_customers_and_tenant_wide_only_data(db_session, tenant_factory):
    tenant, _house_account, users = tenant_factory.make(slug="export-customer-scope")
    customer_a = customer_service.create_customer(db_session, tenant.id, customer_service.CustomerInput(customer_number="C-A", name="A"))
    customer_b = customer_service.create_customer(db_session, tenant.id, customer_service.CustomerInput(customer_number="C-B", name="B"))
    account_a = account_service.create_account(db_session, tenant.id, account_service.AccountInput(account_number="A-1", name="A Acct", customer_id=customer_a.id))
    account_b = account_service.create_account(db_session, tenant.id, account_service.AccountInput(account_number="B-1", name="B Acct", customer_id=customer_b.id))
    issued_item_service.create_issued_item(
        db_session, tenant.id,
        issued_item_service.IssuedItemInput(account_id=account_a.id, check_number="A1", amount=Decimal("10.00"), payee_name="X", issue_date=date(2026, 1, 1)),
        submitted_by_user_id=users["preparer"].id,
    )
    issued_item_service.create_issued_item(
        db_session, tenant.id,
        issued_item_service.IssuedItemInput(account_id=account_b.id, check_number="B1", amount=Decimal("20.00"), payee_name="Y", issue_date=date(2026, 1, 1)),
        submitted_by_user_id=users["preparer"].id,
    )
    db_session.commit()

    job = data_export_service.request_export(db_session, tenant.id, users["admin"].id, customer_id=customer_a.id)
    db_session.commit()
    _run_job_sync(db_session, job)
    db_session.expire_all()
    job = db_session.get(type(job), job.id)

    with zipfile.ZipFile(job.archive_path) as zf:
        names = zf.namelist()
        assert "data/customers.json" not in names
        assert "data/audit_log.json" not in names
        assert "data/bulk_upload_files.json" not in names
        assert "data/customer.json" in names

        issued_items = _read_json(zf, "data/issued_items.json")
        assert [i["check_number"] for i in issued_items] == ["A1"]

        accounts = _read_json(zf, "data/accounts.json")
        assert [a["account_number"] for a in accounts] == ["A-1"]

        customer_profile = _read_json(zf, "data/customer.json")
        assert customer_profile["id"] == str(customer_a.id)


def test_failed_export_marks_job_failed_with_error_message(db_session, tenant_factory, monkeypatch):
    tenant, _account, users = tenant_factory.make(slug="export-failure")
    job = data_export_service.request_export(db_session, tenant.id, users["admin"].id)
    db_session.commit()

    def _boom(*args, **kwargs):
        raise RuntimeError("disk full")

    monkeypatch.setattr(data_export_service, "_build_archive", _boom)
    _run_job_sync(db_session, job)

    db_session.expire_all()
    job = db_session.get(type(job), job.id)
    assert job.status == DataExportJobStatus.FAILED
    assert "disk full" in job.error_message


def test_no_exporter_ever_emits_a_secret_column(db_session, tenant_factory):
    """Tripwire against ANY exporter (today's or a future one) leaking a secret
    column — not just users.json/sso_connections.json, which
    test_export_never_includes_secret_columns above already checks by name. Walks every
    data/*.json file in a rich, fully-populated tenant-wide export and asserts none of
    _SECRET_COLUMNS appears as a key anywhere in it."""
    from openclear.domain.sso_connection import SsoProvider
    from openclear.services import sso_service
    from openclear.services.sso_service import SsoConnectionInput

    tenant, account, users = tenant_factory.make(slug="export-secret-tripwire")
    issued_item_service.create_issued_item(
        db_session, tenant.id,
        issued_item_service.IssuedItemInput(account_id=account.id, check_number="1", amount=Decimal("10.00"), payee_name="X", issue_date=date(2026, 1, 1)),
        submitted_by_user_id=users["preparer"].id,
    )
    db_session.commit()
    ingest_paid_item(db_session, tenant.id, PaidItemSubmission(account_id=account.id, check_number="1", presented_amount=Decimal("12.00"), presented_date=date(2026, 1, 10)))
    db_session.commit()
    sso_service.create_connection(
        db_session, tenant.id,
        SsoConnectionInput(
            provider=SsoProvider.OKTA, display_name="Okta", issuer="https://idp.example.com", client_id="cid",
            client_secret="hunter2-secret", groups_claim_name="groups", auto_provision=False, customer_id=None,
        ),
    )
    db_session.commit()

    job = data_export_service.request_export(db_session, tenant.id, users["admin"].id)
    db_session.commit()
    _run_job_sync(db_session, job)
    db_session.expire_all()
    job = db_session.get(type(job), job.id)
    assert job.status == DataExportJobStatus.COMPLETED

    def _walk_keys(value):
        if isinstance(value, dict):
            for key, sub_value in value.items():
                yield key
                yield from _walk_keys(sub_value)
        elif isinstance(value, list):
            for item in value:
                yield from _walk_keys(item)

    with zipfile.ZipFile(job.archive_path) as zf:
        data_files = [n for n in zf.namelist() if n.startswith("data/") and n.endswith(".json")]
        assert len(data_files) > 5  # sanity: this really did walk a rich export, not an empty one
        for name in data_files:
            payload = json.loads(zf.read(name))
            leaked = data_export_service._SECRET_COLUMNS & set(_walk_keys(payload))
            assert not leaked, f"{name} leaked secret column(s): {leaked}"


def test_export_streams_every_row_across_batches(db_session, tenant_factory, monkeypatch):
    """Rows are read and written in batches (there's no row limit): a table bigger than
    one batch still comes out whole, as one JSON array."""
    import json
    import zipfile

    monkeypatch.setattr(data_export_service, "_BATCH_SIZE", 2)
    tenant, account, users = tenant_factory.make(slug="export-batches")
    for n in range(5):
        issued_item_service.create_issued_item(
            db_session, tenant.id,
            issued_item_service.IssuedItemInput(account_id=account.id, check_number=f"B{n}", amount=Decimal("10.00"), payee_name="X", issue_date=date(2026, 1, 1)),
            submitted_by_user_id=users["preparer"].id,
        )
    db_session.commit()
    destination = data_export_service.data_export_storage.export_archive_path(tenant.id, uuid.uuid4())

    data_export_service._build_archive(db_session, tenant.id, None, destination, timeout_seconds=60)

    with zipfile.ZipFile(destination) as archive:
        rows = json.loads(archive.read("data/issued_items.json"))
    assert sorted(r["check_number"] for r in rows) == ["B0", "B1", "B2", "B3", "B4"]


def test_export_fails_when_the_time_budget_is_exhausted(db_session, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="export-timeout")
    issued_item_service.create_issued_item(
        db_session, tenant.id,
        issued_item_service.IssuedItemInput(account_id=account.id, check_number="1", amount=Decimal("10.00"), payee_name="X", issue_date=date(2026, 1, 1)),
        submitted_by_user_id=users["preparer"].id,
    )
    db_session.commit()

    with pytest.raises(data_export_service.ExportTimedOut):
        data_export_service._build_archive(
            db_session, tenant.id, None, data_export_service.data_export_storage.export_archive_path(tenant.id, uuid.uuid4()),
            timeout_seconds=-1,
        )


def test_run_export_job_fails_gracefully_when_the_archive_cant_be_built(db_session, tenant_factory, monkeypatch):
    tenant, _account, users = tenant_factory.make(slug="export-build-fails")
    job = data_export_service.request_export(db_session, tenant.id, users["admin"].id)
    db_session.commit()

    def broken_build(*args, **kwargs):
        raise RuntimeError("disk full")

    monkeypatch.setattr(data_export_service, "_build_archive", broken_build)
    _run_job_sync(db_session, job)

    db_session.expire_all()
    job = db_session.get(type(job), job.id)
    assert job.status == DataExportJobStatus.FAILED
    assert "disk full" in job.error_message


def test_run_export_job_uses_tenants_own_timeout_override(db_session, tenant_factory, monkeypatch):
    tenant, _account, users = tenant_factory.make(slug="export-timeout-override")
    tenant_service.set_data_export_timeout(db_session, tenant.id, timeout_seconds=7)
    db_session.commit()
    job = data_export_service.request_export(db_session, tenant.id, users["admin"].id)
    db_session.commit()

    captured: dict = {}
    original_build_archive = data_export_service._build_archive

    def _capture(*args, **kwargs):
        captured.update(kwargs)
        return original_build_archive(*args, **kwargs)

    monkeypatch.setattr(data_export_service, "_build_archive", _capture)
    _run_job_sync(db_session, job)

    assert captured["timeout_seconds"] == 7


def test_run_export_job_falls_back_to_global_default_timeout(db_session, tenant_factory, monkeypatch):
    tenant, _account, users = tenant_factory.make(slug="export-timeout-default")
    job = data_export_service.request_export(db_session, tenant.id, users["admin"].id)
    db_session.commit()

    captured: dict = {}
    original_build_archive = data_export_service._build_archive

    def _capture(*args, **kwargs):
        captured.update(kwargs)
        return original_build_archive(*args, **kwargs)

    monkeypatch.setattr(data_export_service, "_build_archive", _capture)
    _run_job_sync(db_session, job)

    assert captured["timeout_seconds"] == get_settings().data_export_timeout_seconds
