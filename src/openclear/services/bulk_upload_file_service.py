# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import uuid
from typing import BinaryIO

from sqlalchemy.orm import Session

from openclear.bulk_import.file_storage import file_digest, save_uploaded_file, save_uploaded_stream
from openclear.bulk_import.signing import content_hash, sign_digest, sign_file, verify_digest_signature
from openclear.db.base import new_uuid
from openclear.domain.bulk_upload_file import BulkUploadFile, BulkUploadKind, BulkUploadSource, BulkUploadStatus
from openclear.repositories.bulk_upload_file_repo import BulkUploadFileRepository


def record_uploaded_file(
    session: Session,
    tenant_id: uuid.UUID,
    *,
    kind: BulkUploadKind,
    filename: str,
    content_type: str | None,
    data: bytes | None = None,
    uploaded_by_user_id: uuid.UUID | None,
    customer_id: uuid.UUID | None = None,
    source: BulkUploadSource = BulkUploadSource.MANUAL,
    stream: BinaryIO | None = None,
) -> BulkUploadFile:
    """Called immediately after reading an uploaded bulk file, before parsing — so a
    rejected/malformed file is still captured for audit purposes, not just successful
    ones. succeeded_count/failed_count start out unset; the caller fills them in
    afterward via set_result_counts once parsing has actually produced row results.
    `customer_id` is the uploader's own scope at upload time (None = tenant-wide staff,
    whose file could legitimately span several customers) — see
    domain/bulk_upload_file.py. `uploaded_by_user_id` is None for an auto-imported file
    (services/dropbox_import_service.py) — no human triggered it.

    Give either `data` or `stream` (a file-like object, such as an upload's spooled
    file): a stream is copied to disk in pieces, hashed and signed as it goes, so a large
    file is never read into memory."""
    upload_id = new_uuid()
    if stream is not None:
        storage_path, size, digest = save_uploaded_stream(tenant_id, upload_id, filename, stream)
        sha256_hex, signature_hex = digest.hex(), sign_digest(digest)
    else:
        storage_path = save_uploaded_file(tenant_id, upload_id, filename, data)
        size, sha256_hex, signature_hex = len(data), content_hash(data), sign_file(data)
    record = BulkUploadFile(
        id=upload_id,
        customer_id=customer_id,
        kind=kind,
        source=source,
        original_filename=filename,
        content_type=content_type,
        storage_path=storage_path,
        size_bytes=size,
        sha256_hex=sha256_hex,
        signature_hex=signature_hex,
        uploaded_by_user_id=uploaded_by_user_id,
        status=BulkUploadStatus.RECEIVED,
        processed_rows=0,
    )
    BulkUploadFileRepository(session, tenant_id, customer_id).add(record)
    session.flush()
    return record


def set_result_counts(session: Session, record: BulkUploadFile, *, succeeded_count: int, failed_count: int) -> None:
    record.succeeded_count = succeeded_count
    record.failed_count = failed_count
    record.status = BulkUploadStatus.COMPLETED
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
    digest = file_digest(record.storage_path)
    return digest.hex() == record.sha256_hex and verify_digest_signature(digest, record.signature_hex)
