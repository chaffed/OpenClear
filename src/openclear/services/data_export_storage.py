# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import uuid
from pathlib import Path

from openclear.config import get_settings


def export_archive_path(tenant_id: uuid.UUID, job_id: uuid.UUID) -> Path:
    """Where a given job's archive lives (or will live) on disk — same local-filesystem,
    DB-only-stores-the-path pattern as ocr/storage.py, web/branding_storage.py, and
    bulk_import/file_storage.py. Exposed as a path (not just a save/read pair) because
    services/data_export_service.py writes the zip incrementally straight to this path
    rather than building the whole archive in memory first."""
    settings = get_settings()
    directory = Path(settings.data_export_storage_dir) / str(tenant_id)
    directory.mkdir(parents=True, exist_ok=True)
    return directory / f"{job_id}.zip"


def read_export_archive(path: str) -> bytes:
    return Path(path).read_bytes()
