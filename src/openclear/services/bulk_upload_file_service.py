# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import uuid

from sqlalchemy.orm import Session

from openclear.bulk_import.file_storage import read_uploaded_file, save_uploaded_file
from openclear.bulk_import.signing import content_hash, sign_file, verify_signature
from openclear.domain.bulk_upload_file import BulkUploadFile, BulkUploadKind, BulkUploadSource
from openclear.repositories.bulk_upload_file_repo import BulkUploadFileRepository


def record_uploaded_file(
    session: Session,
    tenant_id: uuid.UUID,
    *,
    kind: BulkUploadKind,
    filename: str,
    content_type: str | None,
    data: bytes,
    uploaded_by_user_id: uuid.UUID | None,
    customer_id: uuid.UUID | None = None,
    source: BulkUploadSource = BulkUploadSource.MANUAL,
) -> BulkUploadFile:
    """Called immediately after reading an uploaded bulk file, before parsing — so a
    rejected/malformed file is still captured for audit purposes, not just successful
    ones. succeeded_count/failed_count start out unset; the caller fills them in
    afterward via set_result_counts once parsing has actually produced row results.
    `customer_id` is the uploader's own scope at upload time (None = tenant-wide staff,
    whose file could legitimately span several customers) — see
    domain/bulk_upload_file.py. `uploaded_by_user_id` is None for an auto-imported file
    (services/dropbox_import_service.py) — no human triggered it."""
    repo = BulkUploadFileRepository(session, tenant_id, customer_id)
    record = BulkUploadFile(
        customer_id=customer_id,
        kind=kind,
        source=source,
        original_filename=filename,
        content_type=content_type,
        storage_path="",
        size_bytes=len(data),
        sha256_hex=content_hash(data),
        signature_hex=sign_file(data),
        uploaded_by_user_id=uploaded_by_user_id,
    )
    repo.add(record)
    session.flush()  # assign id, needed for the file name

    record.storage_path = save_uploaded_file(tenant_id, record.id, filename, data)
    session.flush()
    return record


def set_result_counts(session: Session, record: BulkUploadFile, *, succeeded_count: int, failed_count: int) -> None:
    record.succeeded_count = succeeded_count
    record.failed_count = failed_count
    session.flush()


def get_uploaded_file(
    session: Session, tenant_id: uuid.UUID, upload_id: uuid.UUID, customer_id: uuid.UUID | None = None
) -> BulkUploadFile | None:
    return BulkUploadFileRepository(session, tenant_id, customer_id).get(upload_id)


def find_by_content_hash(session: Session, tenant_id: uuid.UUID, sha256_hex: str) -> BulkUploadFile | None:
    """Used by services/dropbox_import_service.py to detect a byte-for-byte re-dropped
    file already imported for this tenant, so it's skipped rather than reprocessed."""
    matches = BulkUploadFileRepository(session, tenant_id).list(sha256_hex=sha256_hex)
    return matches[0] if matches else None


def verify_uploaded_file(
    session: Session, tenant_id: uuid.UUID, upload_id: uuid.UUID, customer_id: uuid.UUID | None = None
) -> bool | None:
    """Re-reads the file from disk RIGHT NOW and recomputes/verifies both the hash and
    signature against what was stored at upload time — this is what actually proves
    nothing has changed since upload, as opposed to just checking the stored values are
    internally consistent with each other. Returns None if the record doesn't exist."""
    record = get_uploaded_file(session, tenant_id, upload_id, customer_id)
    if record is None:
        return None
    data = read_uploaded_file(record.storage_path)
    return content_hash(data) == record.sha256_hex and verify_signature(data, record.signature_hex)
