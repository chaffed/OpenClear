# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import contextlib
import uuid
from pathlib import Path

from openclear.config import get_settings


def save_uploaded_file(tenant_id: uuid.UUID, upload_id: uuid.UUID, filename: str, data: bytes) -> str:
    """Local filesystem storage, same pattern as ocr/storage.py::save_image and
    web/branding_storage.py::save_tenant_asset — the DB only ever stores this path, never
    the blob itself. The id-prefixed filename avoids collisions between uploads that
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
    settings = get_settings()
    safe_filename = Path(filename).name or "upload"
    directory = Path(settings.bulk_upload_storage_dir) / str(tenant_id)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{upload_id}_{safe_filename}"
    path.write_bytes(data)
    with contextlib.suppress(OSError):
        path.chmod(0o444)
    return str(path)


def read_uploaded_file(path: str) -> bytes:
    return Path(path).read_bytes()
