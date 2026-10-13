#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Copies files written to local disk into S3-compatible or Azure Blob storage and points the database
at the copies (SCALE_PLAN.md S6). Run it after setting OPENCLEAR_STORAGE_BACKEND=s3 or azure
(and its settings), from a machine that can see the old directories:

    python scripts/move_files_to_object_storage.py --dry-run
    python scripts/move_files_to_object_storage.py
    python scripts/move_files_to_object_storage.py --delete-local   # once you've checked

It's safe to run again: rows that already point at object storage are skipped, and a
file that's missing on disk is reported and left alone. Each file is uploaded under the
same key layout new files use, and a row is updated only after its upload succeeded.
Local files are kept unless --delete-local is given. Connect as the database owner (the
migration role), which isn't subject to row-level security.
"""

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

# (model attribute path, storage area, whether keys carry the bank's id)
_COLUMNS = [
    ("check_image", "front_image_path", "check_images"),
    ("check_image", "back_image_path", "check_images"),
    ("tenant", "logo_path", "tenant_assets"),
    ("tenant", "favicon_path", "tenant_assets"),
    ("bulk_upload_file", "storage_path", "bulk_uploads"),
    ("data_export_job", "archive_path", "data_exports"),
    ("ml_model", "artifact_path", "ml_artifacts"),
]


def _key(area: str, ref: str, tenant_id) -> str:
    """The file's path under its area's directory when it's there (the layout new files
    use); otherwise the bank's id and the file name."""
    from openclear.config import get_settings
    from openclear.storage import AREAS

    base = Path(getattr(get_settings(), AREAS[area])).resolve()
    path = Path(ref).resolve()
    try:
        return path.relative_to(base).as_posix()
    except ValueError:
        return f"{tenant_id}/{path.name}" if tenant_id else path.name


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="List what would move; change nothing")
    parser.add_argument("--delete-local", action="store_true", help="Delete each local file once its copy is recorded")
    args = parser.parse_args()

    import openclear.domain  # noqa: F401 -- registers every model
    from sqlalchemy import select

    from openclear import storage
    from openclear.config import get_settings
    from openclear.db.base import Base
    from openclear.db.session import get_session_factory

    if get_settings().storage_backend not in ("s3", "azure"):
        sys.exit("Set OPENCLEAR_STORAGE_BACKEND=s3 or azure (and its OPENCLEAR_S3_* or OPENCLEAR_AZURE_STORAGE_* settings) first.")

    tables = {mapper.class_.__tablename__: mapper.class_ for mapper in Base.registry.mappers}
    session = get_session_factory()()
    moved = missing = skipped = 0
    try:
        for table, column, area in _COLUMNS:
            model = tables[table]
            attribute = getattr(model, column)
            for row in session.execute(select(model).where(attribute.is_not(None))).scalars():
                ref = getattr(row, column)
                if not ref or storage.is_object_ref(ref):
                    skipped += 1
                    continue
                if not os.path.exists(ref):
                    print(f"  missing on disk, left as is: {table}.{column} {row.id}: {ref}")
                    missing += 1
                    continue
                tenant_id = getattr(row, "tenant_id", None) if table != "tenant" else row.id
                key = _key(area, ref, tenant_id)
                print(f"  {'would move' if args.dry_run else 'moving'} {ref} -> {area}/{key}")
                if args.dry_run:
                    moved += 1
                    continue
                with open(ref, "rb") as handle:
                    new_ref, _size, _digest = storage.backend().put_stream(area, key, handle)
                setattr(row, column, new_ref)
                session.commit()  # one row at a time: an interrupted run loses nothing
                if args.delete_local:
                    storage.LocalStorage().delete(ref)
                moved += 1
    finally:
        session.close()
    verb = "would move" if args.dry_run else "moved"
    print(f"\n{moved} files {verb}, {skipped} already in object storage, {missing} missing on disk.")


if __name__ == "__main__":
    main()
