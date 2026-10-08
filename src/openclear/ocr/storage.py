# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import uuid

from openclear import storage


def save_image(tenant_id: uuid.UUID, check_image_id: uuid.UUID, side: str, image_bytes: bytes) -> str:
    """Stores a check image side (storage.py: local disk or object storage) and returns
    its reference. The DB only ever stores the reference, never the blob itself — keeps
    rows small and portable across SQLite/Postgres/MSSQL."""
    return storage.put("check_images", f"{tenant_id}/{check_image_id}_{side}.png", image_bytes)


def read_image(ref: str) -> bytes:
    return storage.read(ref)
