# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import io
import os
import shutil
import tempfile
import time
import zipfile
from pathlib import Path

import pytest

from openclear.config import get_settings
from openclear.domain.bulk_upload_file import BulkUploadSource
from openclear.repositories.bulk_upload_file_repo import BulkUploadFileRepository
from openclear.repositories.issued_item_repo import IssuedItemRepository
from openclear.repositories.paid_item_repo import PaidItemRepository
from openclear.services import customer_service, dropbox_import_service
from openclear.services.customer_service import CustomerInput

_OLD_ENOUGH = 60  # seconds in the past -- comfortably older than the default 30s age gate


def _configure_dropbox(monkeypatch, tmp_path) -> Path:
    root = tmp_path / "dropbox"
    monkeypatch.setenv("OPENCLEAR_AUTO_IMPORT_DROPBOX_DIR", str(root))
    get_settings.cache_clear()
    return root


def _drop(root: Path, tenant_slug: str, kind_subpath: str, filename: str, content: bytes, *, age_seconds: int = _OLD_ENOUGH) -> Path:
    inbox = root / tenant_slug / kind_subpath / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    path = inbox / filename
    path.write_bytes(content)
    old_time = time.time() - age_seconds
    os.utime(path, (old_time, old_time))
    return path


def _issued_items_csv(rows: list[tuple[str, str, str]]) -> bytes:
    lines = ["account_number,check_number,amount,payee_name,issue_date"]
    for account_number, check_number, amount in rows:
        lines.append(f"{account_number},{check_number},{amount},Vendor,2026-01-01")
    return ("\n".join(lines) + "\n").encode()


def _make_zip(entries: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    return buffer.getvalue()


@pytest.fixture(autouse=True)
def _clear_settings_cache_after():
    yield
    get_settings.cache_clear()


def test_imports_tabular_issued_items_and_moves_to_processed(db_session, tenant_factory, monkeypatch, tmp_path):
    root = _configure_dropbox(monkeypatch, tmp_path)
    tenant, account, _users = tenant_factory.make(slug="dropbox-issued-basic")
    path = _drop(root, tenant.slug, "issued_items", "batch1.csv", _issued_items_csv([(account.account_number, "9001", "150.00")]))

    results = dropbox_import_service.scan_and_import_tenant(db_session, tenant)

    assert len(results) == 1
    assert results[0].outcome == "imported"
    assert results[0].succeeded_count == 1
    assert results[0].failed_count == 0
    assert not path.exists()
    assert (root / tenant.slug / "issued_items" / "processed" / "batch1.csv").exists()

    items = IssuedItemRepository(db_session, tenant.id).list()
    assert len(items) == 1
    assert items[0].check_number == "9001"

    uploads = BulkUploadFileRepository(db_session, tenant.id).list()
    assert len(uploads) == 1
    assert uploads[0].source == BulkUploadSource.AUTO_IMPORT
    assert uploads[0].uploaded_by_user_id is None


def test_file_younger_than_age_gate_is_left_alone(db_session, tenant_factory, monkeypatch, tmp_path):
    root = _configure_dropbox(monkeypatch, tmp_path)
    tenant, account, _users = tenant_factory.make(slug="dropbox-age-gate")
    path = _drop(
        root, tenant.slug, "issued_items", "fresh.csv",
        _issued_items_csv([(account.account_number, "9001", "150.00")]), age_seconds=1,
    )

    results = dropbox_import_service.scan_and_import_tenant(db_session, tenant)

    assert results == []
    assert path.exists()
    assert IssuedItemRepository(db_session, tenant.id).list() == []


def test_duplicate_content_is_skipped_and_left_in_place(db_session, tenant_factory, monkeypatch, tmp_path):
    root = _configure_dropbox(monkeypatch, tmp_path)
    tenant, account, _users = tenant_factory.make(slug="dropbox-dedup")
    content = _issued_items_csv([(account.account_number, "9001", "150.00")])
    _drop(root, tenant.slug, "issued_items", "batch1.csv", content)
    dropbox_import_service.scan_and_import_tenant(db_session, tenant)
    assert len(IssuedItemRepository(db_session, tenant.id).list()) == 1

    # Same bytes, different filename -- a legitimate re-send scenario the dedup check
    # must still catch, since it fingerprints content, not filename.
    dup_path = _drop(root, tenant.slug, "issued_items", "batch1-resend.csv", content)

    results = dropbox_import_service.scan_and_import_tenant(db_session, tenant)

    assert len(results) == 1
    assert results[0].outcome == "duplicate"
    assert dup_path.exists()  # left in place, not moved
    assert len(IssuedItemRepository(db_session, tenant.id).list()) == 1  # not re-imported
    assert len(BulkUploadFileRepository(db_session, tenant.id).list()) == 1  # no second audit row


def test_malformed_file_moves_to_failed_and_does_not_block_the_rest_of_the_scan(db_session, tenant_factory, monkeypatch, tmp_path):
    root = _configure_dropbox(monkeypatch, tmp_path)
    tenant, account, _users = tenant_factory.make(slug="dropbox-bad-file")
    bad_path = _drop(root, tenant.slug, "issued_items", "bad.csv", b"not,even,a,real,header\n")
    good_path = _drop(
        root, tenant.slug, "checks/tabular", "checks.csv",
        (
            "account_number,check_number,presented_amount,presented_date\n"
            f"{account.account_number},5001,25.00,2026-01-15\n"
        ).encode(),
    )

    results = dropbox_import_service.scan_and_import_tenant(db_session, tenant)

    outcomes = {r.filename: r for r in results}
    assert outcomes["bad.csv"].outcome == "failed"
    assert not bad_path.exists()
    assert (root / tenant.slug / "issued_items" / "failed" / "bad.csv").exists()

    assert outcomes["checks.csv"].outcome == "imported"
    assert not good_path.exists()
    assert len(PaidItemRepository(db_session, tenant.id).list()) == 1


def test_oversized_file_rejected_unread(db_session, tenant_factory, monkeypatch, tmp_path):
    root = _configure_dropbox(monkeypatch, tmp_path)
    monkeypatch.setenv("OPENCLEAR_AUTO_IMPORT_MAX_FILE_BYTES", "10")
    get_settings.cache_clear()
    tenant, account, _users = tenant_factory.make(slug="dropbox-too-big")
    path = _drop(root, tenant.slug, "issued_items", "big.csv", _issued_items_csv([(account.account_number, "9001", "150.00")]))

    results = dropbox_import_service.scan_and_import_tenant(db_session, tenant)

    assert results[0].outcome == "failed"
    assert results[0].error == "File too large"
    assert not path.exists()
    assert (root / tenant.slug / "issued_items" / "failed" / "big.csv").exists()
    assert BulkUploadFileRepository(db_session, tenant.id).list() == []


def test_customer_subdirectory_is_scoped_to_that_customer(db_session, tenant_factory, monkeypatch, tmp_path):
    root = _configure_dropbox(monkeypatch, tmp_path)
    tenant, _account, _users = tenant_factory.make(slug="dropbox-customer-scope")
    customer = customer_service.create_customer(
        db_session, tenant.id, CustomerInput(customer_number="CUST1", name="Client One")
    )
    from openclear.services import account_service

    account = account_service.create_account(
        db_session, tenant.id, account_service.AccountInput(account_number="7777", name="Client Acct", customer_id=customer.id)
    )
    db_session.commit()

    _drop(
        root, tenant.slug, f"CUST1/issued_items", "batch1.csv",
        _issued_items_csv([(account.account_number, "9001", "150.00")]),
    )

    results = dropbox_import_service.scan_and_import_tenant(db_session, tenant)

    assert len(results) == 1
    assert results[0].outcome == "imported"
    items = IssuedItemRepository(db_session, tenant.id, customer.id).list()
    assert len(items) == 1

    uploads = BulkUploadFileRepository(db_session, tenant.id, customer.id).list()
    assert len(uploads) == 1
    assert uploads[0].customer_id == customer.id


def test_scan_scoped_to_one_customer_id_never_touches_tenant_wide_or_other_customer_files(
    db_session, tenant_factory, monkeypatch, tmp_path
):
    root = _configure_dropbox(monkeypatch, tmp_path)
    tenant, account, _users = tenant_factory.make(slug="dropbox-customer-isolation")
    customer_a = customer_service.create_customer(db_session, tenant.id, CustomerInput(customer_number="A", name="Customer A"))
    customer_b = customer_service.create_customer(db_session, tenant.id, CustomerInput(customer_number="B", name="Customer B"))
    from openclear.services import account_service

    account_a = account_service.create_account(
        db_session, tenant.id, account_service.AccountInput(account_number="1111", name="A Acct", customer_id=customer_a.id)
    )
    account_b = account_service.create_account(
        db_session, tenant.id, account_service.AccountInput(account_number="2222", name="B Acct", customer_id=customer_b.id)
    )
    db_session.commit()

    _drop(root, tenant.slug, "A/issued_items", "a.csv", _issued_items_csv([(account_a.account_number, "1001", "10.00")]))
    _drop(root, tenant.slug, "B/issued_items", "b.csv", _issued_items_csv([(account_b.account_number, "2001", "20.00")]))
    _drop(root, tenant.slug, "issued_items", "tenant-wide.csv", _issued_items_csv([(account.account_number, "3001", "30.00")]))

    results = dropbox_import_service.scan_and_import_tenant(db_session, tenant, customer_id=customer_a.id)

    assert len(results) == 1
    assert results[0].filename == "a.csv"
    assert len(IssuedItemRepository(db_session, tenant.id, customer_a.id).list()) == 1
    assert len(IssuedItemRepository(db_session, tenant.id, customer_b.id).list()) == 0
    # tenant-wide.csv untouched -- customer-scoped scan never reaches the tenant root
    assert (root / tenant.slug / "issued_items" / "inbox" / "tenant-wide.csv").exists()
    assert (root / tenant.slug / "B" / "issued_items" / "inbox" / "b.csv").exists()


def test_two_tenants_do_not_see_each_others_files(db_session, tenant_factory, monkeypatch, tmp_path):
    root = _configure_dropbox(monkeypatch, tmp_path)
    tenant_a, account_a, _ = tenant_factory.make(slug="dropbox-tenant-a")
    tenant_b, account_b, _ = tenant_factory.make(slug="dropbox-tenant-b")

    _drop(root, tenant_a.slug, "issued_items", "a.csv", _issued_items_csv([(account_a.account_number, "1001", "10.00")]))
    _drop(root, tenant_b.slug, "issued_items", "b.csv", _issued_items_csv([(account_b.account_number, "2001", "20.00")]))

    results_a = dropbox_import_service.scan_and_import_tenant(db_session, tenant_a)

    assert len(results_a) == 1
    assert results_a[0].filename == "a.csv"
    assert len(IssuedItemRepository(db_session, tenant_a.id).list()) == 1
    assert len(IssuedItemRepository(db_session, tenant_b.id).list()) == 0
    # tenant_b's file is untouched by tenant_a's scan
    assert (root / tenant_b.slug / "issued_items" / "inbox" / "b.csv").exists()


def test_unrecognized_tenant_slug_directory_is_left_alone(db_session, tenant_factory, monkeypatch, tmp_path):
    root = _configure_dropbox(monkeypatch, tmp_path)
    tenant, account, _users = tenant_factory.make(slug="dropbox-real-tenant")
    _drop(root, tenant.slug, "issued_items", "real.csv", _issued_items_csv([(account.account_number, "9001", "150.00")]))
    bogus_path = _drop(root, "typo-d-slug", "issued_items", "orphan.csv", _issued_items_csv([("0001", "9002", "50.00")]))

    results = dropbox_import_service.scan_and_import_all_tenants(db_session)

    assert len(results) == 1
    assert results[0].filename == "real.csv"
    assert bogus_path.exists()


def test_ach_tabular_and_checks_tabular_dispatch(db_session, tenant_factory, monkeypatch, tmp_path):
    root = _configure_dropbox(monkeypatch, tmp_path)
    tenant, account, _users = tenant_factory.make(slug="dropbox-ach-checks")
    ach_csv = (
        "account_number,originator_id,originator_name,receiver_id,amount,transaction_type,sec_code,trace_number,effective_date\n"
        f"{account.account_number},ORIG1,Payroll Co,,10.00,credit,PPD,T1,2026-01-10\n"
    ).encode()
    checks_csv = (
        "account_number,check_number,presented_amount,presented_date\n"
        f"{account.account_number},5001,25.00,2026-01-15\n"
    ).encode()
    _drop(root, tenant.slug, "ach_transactions/tabular", "ach.csv", ach_csv)
    _drop(root, tenant.slug, "checks/tabular", "checks.csv", checks_csv)

    results = dropbox_import_service.scan_and_import_tenant(db_session, tenant)

    outcomes = {r.filename: r for r in results}
    assert outcomes["ach.csv"].outcome == "imported"
    assert outcomes["checks.csv"].outcome == "imported"
    assert len(PaidItemRepository(db_session, tenant.id).list()) == 1


def test_zip_manifest_dispatch(db_session, tenant_factory, monkeypatch, tmp_path):
    root = _configure_dropbox(monkeypatch, tmp_path)
    tenant, account, _users = tenant_factory.make(slug="dropbox-zip")

    content = _make_zip(
        {
            "manifest.csv": (
                f"account_number,check_number,amount,presented_date,front_image_filename\n"
                f"{account.account_number},6001,40.00,2026-01-20,front.tif\n"
            ).encode(),
            "front.tif": _tiny_tiff(),
        }
    )
    _drop(root, tenant.slug, "checks/zip", "cashletter.zip", content)

    results = dropbox_import_service.scan_and_import_tenant(db_session, tenant)

    assert len(results) == 1
    assert results[0].outcome == "imported"
    assert results[0].succeeded_count == 1


def _tiny_tiff() -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (4, 4), color="white").save(buffer, format="TIFF")
    return buffer.getvalue()


def test_nacha_dispatch(db_session, tenant_factory, monkeypatch, tmp_path):
    from openclear.repositories.ach_transaction_repo import AchTransactionRepository
    from tests.test_bulk_import.test_nacha import _batch_control, _batch_header, _entry_detail, _entry_spec

    root = _configure_dropbox(monkeypatch, tmp_path)
    tenant, account, _users = tenant_factory.make(slug="dropbox-nacha")

    entries = [_entry_spec()]
    content = "\n".join([_batch_header(), _entry_detail(dfi_account_number=account.account_number), _batch_control(entries)]).encode(
        "ascii"
    )
    _drop(root, tenant.slug, "ach_transactions/nacha", "batch.ach", content)

    results = dropbox_import_service.scan_and_import_tenant(db_session, tenant)

    assert len(results) == 1
    assert results[0].outcome == "imported"
    assert results[0].succeeded_count == 1
    assert len(AchTransactionRepository(db_session, tenant.id).list()) == 1


def test_x937_dispatch(db_session, tenant_factory, monkeypatch, tmp_path):
    from tests.x937_builder import single

    root = _configure_dropbox(monkeypatch, tmp_path)
    tenant, account, _users = tenant_factory.make(slug="dropbox-x937")
    content = single(f"{account.account_number}/", "5001", 15000, [_tiny_tiff()])
    _drop(root, tenant.slug, "checks/x937", "cashletter.x937", content)

    results = dropbox_import_service.scan_and_import_tenant(db_session, tenant)

    assert len(results) == 1
    assert results[0].outcome == "imported"
    assert results[0].succeeded_count == 1
    assert len(PaidItemRepository(db_session, tenant.id).list()) == 1


def test_customer_number_path_traversal_into_sibling_tenant_is_refused(db_session, tenant_factory, monkeypatch, tmp_path):
    """customer_number is a freeform field any customer:manage-permitted user can set or
    edit via a plain web form -- unfiltered, it becomes a raw filesystem path segment
    (root / customer.customer_number), and pathlib/the OS honor '..' at actual
    filesystem-access time. A customer_number of "../victim-tenant" must never let this
    tenant's scan read or move a SIBLING tenant's own dropbox files.

    The attacker's own tenant directory is created first (root/attacker-tenant/) -- for
    '..' to resolve at all, the OS must be able to actually traverse into that
    intermediate directory, which is exactly the realistic case for any tenant that
    already uses this feature for its own legitimate files. Without that, this test
    would pass even with the vulnerability wide open, for the wrong reason (nothing to
    traverse from) -- confirmed by first reproducing that false-positive directly against
    the fix disabled, before writing this version."""
    root = _configure_dropbox(monkeypatch, tmp_path)
    attacker_tenant, attacker_account, _ = tenant_factory.make(slug="attacker-tenant")
    victim_tenant, victim_account, _ = tenant_factory.make(slug="victim-tenant")
    (root / attacker_tenant.slug).mkdir(parents=True)

    customer = customer_service.create_customer(
        db_session, attacker_tenant.id, CustomerInput(customer_number="../victim-tenant", name="Malicious")
    )
    db_session.commit()

    # A real file sitting in the victim tenant's own inbox, never intended for the attacker.
    _drop(
        root, victim_tenant.slug, "issued_items", "victim.csv",
        _issued_items_csv([(victim_account.account_number, "9999", "999.00")]),
    )

    results = dropbox_import_service.scan_and_import_tenant(db_session, attacker_tenant, customer_id=customer.id)

    assert results == []
    # the victim's file must be untouched -- not read, not moved, not imported anywhere
    assert (root / victim_tenant.slug / "issued_items" / "inbox" / "victim.csv").exists()
    assert IssuedItemRepository(db_session, victim_tenant.id).list() == []
    assert IssuedItemRepository(db_session, attacker_tenant.id).list() == []


def test_customer_number_absolute_path_is_refused(db_session, tenant_factory, monkeypatch, tmp_path, request):
    """pathlib's '/' operator discards everything to its left when the right-hand
    segment looks absolute (Path("/a/b") / "/etc" == Path("/etc")) -- a customer_number
    that looks like an absolute path must never let the scanner read from or move files
    in an arbitrary filesystem location outside the configured dropbox root entirely."""
    root = _configure_dropbox(monkeypatch, tmp_path)
    tenant, _account, _users = tenant_factory.make(slug="absolute-path-tenant")

    # A short absolute path: customer_number is VARCHAR(64), which Postgres enforces, and
    # pytest's tmp_path alone can be longer than that.
    outside_target = Path(tempfile.mkdtemp(prefix="pp-out-", dir="/tmp"))
    request.addfinalizer(lambda: shutil.rmtree(outside_target, ignore_errors=True))
    outside_inbox = outside_target / "issued_items" / "inbox"
    outside_inbox.mkdir(parents=True)
    sensitive = outside_inbox / "sensitive.csv"
    sensitive.write_text("not a real issued-items file -- just proving it's untouched")
    old = time.time() - _OLD_ENOUGH
    os.utime(sensitive, (old, old))

    customer = customer_service.create_customer(
        db_session, tenant.id, CustomerInput(customer_number=str(outside_target), name="Malicious")
    )
    db_session.commit()

    results = dropbox_import_service.scan_and_import_tenant(db_session, tenant, customer_id=customer.id)

    assert results == []
    assert sensitive.exists()
    assert sensitive.read_text() == "not a real issued-items file -- just proving it's untouched"


def test_tenant_slug_path_traversal_is_refused(db_session, tenant_factory, monkeypatch, tmp_path):
    """Same class of risk as customer_number, for tenant.slug -- unvalidated anywhere at
    creation, and also joined directly into a filesystem path."""
    root = _configure_dropbox(monkeypatch, tmp_path)
    root.mkdir(parents=True)
    tenant, account, _users = tenant_factory.make(slug="tenant-with-bad-slug")

    # A real file sitting where "../escaped" would resolve to, right alongside the
    # dropbox root itself -- never intended to be reachable by any tenant's own scan.
    _drop(root.parent, "escaped", "issued_items", "escaped.csv", _issued_items_csv([(account.account_number, "7001", "70.00")]))
    escaped_inbox = root.parent / "escaped" / "issued_items" / "inbox"

    tenant.slug = "../escaped"
    db_session.commit()

    results = dropbox_import_service.scan_and_import_tenant(db_session, tenant)

    assert results == []
    assert (escaped_inbox / "escaped.csv").exists()
    assert IssuedItemRepository(db_session, tenant.id).list() == []


def test_malware_in_the_drop_folder_is_refused(db_session, tenant_factory, monkeypatch, tmp_path):
    """The drop folder doesn't come through HTTP, so it's scanned here (OWASP_PLAN.md OW-D34)."""
    from tests.test_web.test_malware import EICAR, FakeClamd

    root = _configure_dropbox(monkeypatch, tmp_path)
    clamd = FakeClamd()
    monkeypatch.setattr(get_settings(), "malware_clamd_address", clamd.address)
    try:
        tenant, account, _users = tenant_factory.make(slug="dropbox-malware")
        path = _drop(root, tenant.slug, "issued_items", "bad.csv", _issued_items_csv([(account.account_number, "9001", "1.00")]) + EICAR)

        results = dropbox_import_service.scan_and_import_tenant(db_session, tenant)
    finally:
        clamd.close()

    assert results[0].outcome == "failed" and "malware" in results[0].error
    assert not path.exists() and (root / tenant.slug / "issued_items" / "failed" / "bad.csv").exists()
    assert BulkUploadFileRepository(db_session, tenant.id).list() == []
