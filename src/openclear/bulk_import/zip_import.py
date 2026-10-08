# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Parses a ZIP file containing exactly one tabular manifest (CSV/TSV/Excel — the check's
encoded data) plus the image files it references by filename. This is the "ZIP+CSV" bulk
check-image format (see networks/check/bulk_import.py for how the parsed rows/images turn
into paid_item + check_image rows)."""

import io
import zipfile
from collections.abc import Mapping
from pathlib import PurePosixPath
from typing import Any

from openclear.bulk_import.tabular import TabularParseError, parse_tabular_file
from openclear.config import get_settings

_MANIFEST_EXTENSIONS = (".csv", ".tsv", ".xlsx", ".xls")


class ZipImportError(Exception):
    pass


class _LazyImages(Mapping):
    """Images keyed like parse_zip_manifest's dict, read from the archive only when looked
    up, so a large zip isn't held in memory (SCALE_PLAN.md S4)."""

    def __init__(self, archive: zipfile.ZipFile, names: dict[str, str]) -> None:
        self._archive, self._names = archive, names

    def __getitem__(self, key: str) -> bytes:
        return self._archive.read(self._names[key])

    def __iter__(self):
        return iter(self._names)

    def __len__(self) -> int:
        return len(self._names)


def parse_zip_manifest(content, *, lazy_images: bool = False) -> tuple[list[dict[str, Any]], Mapping[str, bytes]]:
    """Returns `(rows, images_by_filename)`. `images_by_filename` is keyed by each zip
    entry's basename, lowercased, so a manifest's `front_image_filename`/
    `back_image_filename` columns can reference an image anywhere in the archive (nested
    folders included) without needing an exact path match. Raises ZipImportError if the
    file isn't a valid zip, or doesn't contain exactly one top-level manifest file.

    `content` is the file's bytes, or a path to it. With lazy_images=True the images are
    read from the archive when looked up instead of all at once; the archive (opened from
    the path) then stays open for as long as the returned mapping is used."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(content) if isinstance(content, (bytes, bytearray)) else content)
    except zipfile.BadZipFile as exc:
        raise ZipImportError(f"Not a valid zip file: {exc}") from exc

    # Classic zip-bomb defense: a small compressed upload can still declare an enormous
    # uncompressed size per entry — check the SUM of every entry's own declared size
    # before reading (and therefore fully decompressing) any of them. This is a
    # first-line, declared-size check, not a substitute for the request-body-size
    # middleware (main.py) that already bounds the compressed upload itself.
    max_uncompressed = get_settings().max_zip_uncompressed_bytes
    total_declared_size = sum(info.file_size for info in archive.infolist() if not info.is_dir())
    if total_declared_size > max_uncompressed:
        raise ZipImportError(
            f"This zip's contents would decompress to {total_declared_size:,} bytes, "
            f"over the {max_uncompressed:,} byte limit."
        )

    manifest_names = []
    images: dict[str, bytes] = {}
    image_names: dict[str, str] = {}
    for info in archive.infolist():
        if info.is_dir():
            continue
        name = info.filename
        basename = PurePosixPath(name).name
        if not basename:
            continue
        lower = basename.lower()
        if lower.endswith(_MANIFEST_EXTENSIONS):
            manifest_names.append(name)
        elif lazy_images:
            image_names[lower] = name
        else:
            images[lower] = archive.read(name)

    if not manifest_names:
        raise ZipImportError(f"No manifest file found in the zip — expected one of {', '.join(_MANIFEST_EXTENSIONS)}")
    if len(manifest_names) > 1:
        raise ZipImportError(f"Found more than one manifest file in the zip: {', '.join(manifest_names)}")

    manifest_name = manifest_names[0]
    try:
        rows = parse_tabular_file(manifest_name, archive.read(manifest_name))
    except TabularParseError as exc:
        raise ZipImportError(f"Could not parse manifest {manifest_name!r}: {exc}") from exc

    return rows, (_LazyImages(archive, image_names) if lazy_images else images)


def find_image(images: Mapping[str, bytes], filename: str) -> bytes | None:
    """Looks up a manifest-referenced filename against the archive's images, matching by
    basename only (case-insensitive) — a manifest column can reference just a filename
    even if the actual entry lives in a subfolder of the zip."""
    basename = PurePosixPath(filename).name
    return images.get(basename.lower())
