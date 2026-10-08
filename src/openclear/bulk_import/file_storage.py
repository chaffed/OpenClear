# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import uuid
from pathlib import Path
from typing import BinaryIO

from openclear import storage


def _key(tenant_id: uuid.UUID, upload_id: uuid.UUID, filename: str) -> str:
    return f"{tenant_id}/{upload_id}_{Path(filename).name or 'upload'}"


def save_uploaded_file(tenant_id: uuid.UUID, upload_id: uuid.UUID, filename: str, data: bytes) -> str:
    """Stored through storage.py (local disk or object storage), same pattern as
    ocr/storage.py::save_image — the DB only ever stores the reference, never the blob
    itself. The id-prefixed filename avoids collisions between uploads that
    happen to share a name; the original name is preserved for the download's
    Content-Disposition header (which is passed `filename` as given — sanitization here
    is for the on-disk path only). Best-effort read-only permissions afterward — a
    defense-in-depth nicety, not what actually detects tampering (that's the ECDSA
    signature in bulk_import/signing.py, re-checked against a fresh read of this file on
    demand).

    `Path(filename).name` strips any directory components (e.g. `../../etc/passwd` ->
    `passwd`) before it's embedded in the path — a client-supplied filename with no
    sanitization here previously reached the path construction directly; that traversal
    attempt didn't actually escape the storage directory (pathlib's write_bytes doesn't
    auto-create missing parent directories, so it just failed with an unhandled
    FileNotFoundError), but that was incidental, not an explicit control, and it meant a
    file with a path-like name crashed instead of uploading cleanly."""
    return storage.put("bulk_uploads", _key(tenant_id, upload_id, filename), data, read_only=True)


def save_uploaded_stream(tenant_id: uuid.UUID, upload_id: uuid.UUID, filename: str, stream: BinaryIO) -> tuple[str, int, bytes]:
    """save_uploaded_file for a file-like object, copied in 1 MB pieces so a large upload
    is never held in memory (SCALE_PLAN.md S4). Returns (reference, size, SHA-256 digest)."""
    return storage.put_stream("bulk_uploads", _key(tenant_id, upload_id, filename), stream, read_only=True)


def file_digest(ref: str) -> bytes:
    """The stored file's SHA-256, read in pieces. Also takes a plain local path (a file
    waiting in a dropbox inbox)."""
    return storage.sha256(ref)


def read_uploaded_file(ref: str) -> bytes:
    return storage.read(ref)
