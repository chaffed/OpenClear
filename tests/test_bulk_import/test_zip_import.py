# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import io
import zipfile

import pytest

from openclear.bulk_import.zip_import import ZipImportError, find_image, parse_zip_manifest


def _make_zip(entries: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    return buffer.getvalue()


def test_parses_manifest_and_indexes_images_by_basename():
    content = _make_zip(
        {
            "manifest.csv": "account_number,check_number\n1001,5001\n".encode(),
            "images/check5001.tif": b"fake-tiff-bytes",
        }
    )
    rows, images = parse_zip_manifest(content)
    assert rows == [{"account_number": "1001", "check_number": "5001"}]
    assert find_image(images, "check5001.tif") == b"fake-tiff-bytes"
    # case-insensitive, and matched by basename regardless of the manifest referencing
    # just a filename even though the actual entry lives in a subfolder
    assert find_image(images, "CHECK5001.TIF") == b"fake-tiff-bytes"


def test_missing_manifest_is_a_clear_error():
    content = _make_zip({"images/check5001.tif": b"fake-tiff-bytes"})
    with pytest.raises(ZipImportError, match="No manifest file"):
        parse_zip_manifest(content)


def test_multiple_manifest_files_is_a_clear_error():
    content = _make_zip(
        {
            "manifest.csv": "a,b\n1,2\n".encode(),
            "other.csv": "a,b\n1,2\n".encode(),
        }
    )
    with pytest.raises(ZipImportError, match="more than one manifest"):
        parse_zip_manifest(content)


def test_not_a_zip_file_is_a_clear_error():
    with pytest.raises(ZipImportError, match="Not a valid zip"):
        parse_zip_manifest(b"this is definitely not a zip file")


def test_find_image_returns_none_when_absent():
    content = _make_zip({"manifest.csv": "a,b\n1,2\n".encode()})
    _rows, images = parse_zip_manifest(content)
    assert find_image(images, "does-not-exist.tif") is None


def test_oversized_declared_uncompressed_size_is_rejected_before_reading_entries(monkeypatch):
    from openclear.config import get_settings

    monkeypatch.setattr(get_settings(), "max_zip_uncompressed_bytes", 10)  # smaller than the content below
    content = _make_zip(
        {
            "manifest.csv": "account_number,check_number\n1001,5001\n".encode(),
            "images/check5001.tif": b"fake-tiff-bytes",
        }
    )
    with pytest.raises(ZipImportError, match="decompress to"):
        parse_zip_manifest(content)


def test_uncompressed_size_within_the_cap_still_parses(monkeypatch):
    from openclear.config import get_settings

    monkeypatch.setattr(get_settings(), "max_zip_uncompressed_bytes", 10_000)
    content = _make_zip({"manifest.csv": "account_number,check_number\n1001,5001\n".encode()})
    rows, _images = parse_zip_manifest(content)
    assert rows == [{"account_number": "1001", "check_number": "5001"}]
