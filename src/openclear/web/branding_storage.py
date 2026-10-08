# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import mimetypes
import uuid
from typing import Literal

from openclear import storage


def save_tenant_asset(tenant_id: uuid.UUID, kind: Literal["logo", "favicon"], content_type: str, data: bytes) -> str:
    """Stores a bank's logo or favicon (storage.py), same pattern as
    ocr/storage.py::save_image — the DB only ever stores the reference, never the blob
    itself. One file per (tenant, kind): a re-upload overwrites the previous logo/favicon
    rather than accumulating old ones, since only the current one is ever served."""
    ext = mimetypes.guess_extension(content_type) or ".bin"
    return storage.put("tenant_assets", f"{tenant_id}/{kind}{ext}", data)


def read_tenant_asset(ref: str) -> bytes:
    return storage.read(ref)
