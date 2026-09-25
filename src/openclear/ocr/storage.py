# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import uuid
from pathlib import Path

from openclear.config import get_settings


def save_image(tenant_id: uuid.UUID, check_image_id: uuid.UUID, side: str, image_bytes: bytes) -> str:
    """Local filesystem storage. The DB only ever stores this path, never the blob itself
    — keeps rows small and portable across SQLite/Postgres/MSSQL. Swappable for S3/Blob
    storage later behind the same signature for multi-instance enterprise deployments."""
    settings = get_settings()
    directory = Path(settings.check_image_storage_dir) / str(tenant_id)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{check_image_id}_{side}.png"
    path.write_bytes(image_bytes)
    return str(path)


def read_image(path: str) -> bytes:
    return Path(path).read_bytes()
