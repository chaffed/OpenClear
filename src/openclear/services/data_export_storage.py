# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import tempfile
import uuid
from pathlib import Path

from openclear import storage


def build_path(job_id: uuid.UUID) -> Path:
    """A local file to build an export's archive in. services/data_export_service.py
    writes the zip incrementally straight to it rather than building the whole archive in
    memory, then store_archive() moves it to storage (storage.py)."""
    return Path(tempfile.gettempdir()) / f"openclear-export-{job_id}.zip"


def store_archive(tenant_id: uuid.UUID, job_id: uuid.UUID, built: Path) -> str:
    """Moves a finished archive into storage and returns its reference."""
    return storage.put_file("data_exports", f"{tenant_id}/{job_id}.zip", built)
