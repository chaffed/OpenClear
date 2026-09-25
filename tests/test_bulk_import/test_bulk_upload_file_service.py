# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

from pathlib import Path

from openclear.domain.bulk_upload_file import BulkUploadKind
from openclear.services import bulk_upload_file_service


def test_record_uploaded_file_saves_hashes_and_signs(db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="bulk-file-record")
    data = b"check_number,amount\n5001,150.00\n"

    record = bulk_upload_file_service.record_uploaded_file(
        db_session,
        tenant.id,
        kind=BulkUploadKind.ISSUED_ITEMS,
        filename="items.csv",
        content_type="text/csv",
        data=data,
        uploaded_by_user_id=users["admin"].id,
    )
    db_session.commit()

    assert record.size_bytes == len(data)
    assert Path(record.storage_path).read_bytes() == data
    assert bulk_upload_file_service.verify_uploaded_file(db_session, tenant.id, record.id) is True


def test_set_result_counts(db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="bulk-file-counts")
    record = bulk_upload_file_service.record_uploaded_file(
        db_session,
        tenant.id,
        kind=BulkUploadKind.USERS,
        filename="users.csv",
        content_type="text/csv",
        data=b"email,security_group,password\n",
        uploaded_by_user_id=users["admin"].id,
    )
    assert record.succeeded_count is None
    assert record.failed_count is None

    bulk_upload_file_service.set_result_counts(db_session, record, succeeded_count=3, failed_count=1)
    db_session.commit()

    assert record.succeeded_count == 3
    assert record.failed_count == 1


def test_verify_uploaded_file_detects_tampering_on_disk(db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="bulk-file-tamper")
    record = bulk_upload_file_service.record_uploaded_file(
        db_session,
        tenant.id,
        kind=BulkUploadKind.ACH_TRANSACTIONS,
        filename="ach.csv",
        content_type="text/csv",
        data=b"originator_id,amount\nORIG1,10.00\n",
        uploaded_by_user_id=users["admin"].id,
    )
    db_session.commit()

    assert bulk_upload_file_service.verify_uploaded_file(db_session, tenant.id, record.id) is True

    # simulate tampering: someone with enough filesystem access to bypass the
    # best-effort read-only permission (chmod 0o444 is defense-in-depth, not what
    # actually detects this — the HMAC re-check below is) edits the saved file directly.
    path = Path(record.storage_path)
    path.chmod(0o644)
    path.write_bytes(b"originator_id,amount\nORIG1,999999.00\n")

    assert bulk_upload_file_service.verify_uploaded_file(db_session, tenant.id, record.id) is False


def test_verify_uploaded_file_unknown_id_returns_none(db_session, tenant_factory):
    import uuid

    tenant, _account, _users = tenant_factory.make(slug="bulk-file-unknown")
    assert bulk_upload_file_service.verify_uploaded_file(db_session, tenant.id, uuid.uuid4()) is None


def test_get_uploaded_file_is_tenant_scoped(db_session, tenant_factory):
    tenant_a, _account_a, users_a = tenant_factory.make(slug="bulk-file-scope-a")
    tenant_b, _account_b, _users_b = tenant_factory.make(slug="bulk-file-scope-b")

    record = bulk_upload_file_service.record_uploaded_file(
        db_session,
        tenant_a.id,
        kind=BulkUploadKind.USERS,
        filename="u.csv",
        content_type="text/csv",
        data=b"x",
        uploaded_by_user_id=users_a["admin"].id,
    )
    db_session.commit()

    assert bulk_upload_file_service.get_uploaded_file(db_session, tenant_b.id, record.id) is None
    assert bulk_upload_file_service.get_uploaded_file(db_session, tenant_a.id, record.id) is not None
