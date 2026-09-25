# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import mimetypes
import uuid
from pathlib import Path
from typing import Literal

from openclear.config import get_settings


def save_tenant_asset(tenant_id: uuid.UUID, kind: Literal["logo", "favicon"], content_type: str, data: bytes) -> str:
    """Local filesystem storage, same pattern as ocr/storage.py::save_image — the DB only
    ever stores this path, never the blob itself. One file per (tenant, kind): a
    re-upload overwrites the previous logo/favicon rather than accumulating old ones,
    since only the current one is ever served."""
    settings = get_settings()
    directory = Path(settings.tenant_asset_storage_dir) / str(tenant_id)
    directory.mkdir(parents=True, exist_ok=True)
    ext = mimetypes.guess_extension(content_type) or ".bin"
    path = directory / f"{kind}{ext}"
    path.write_bytes(data)
    return str(path)


def read_tenant_asset(path: str) -> bytes:
    return Path(path).read_bytes()
