# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Where the app's files live (SCALE_PLAN.md S6): check images, bank logos and favicons,
bulk upload files, data export archives and ML model artifacts.

`storage_backend` picks where new files are written:

- "local" (the default): a directory per kind of file (`check_image_storage_dir` and so
  on), as before. Fine for one instance, or several sharing a network volume.
- "s3": an S3-compatible bucket (AWS S3, MinIO, Cloudflare R2, ...), so several
  instances (and workers) share files without a shared disk. Needs `pip install
  openclear[s3]`.

The database stores a reference to each file, never the bytes. A local file's
reference is its path, exactly as before S6; an object's is `s3://bucket/key`. Reads go
by the reference, not the setting, so files written before a switch to S3 stay readable
(while the disk is still there), and `scripts/move_files_to_object_storage.py` moves
them over and rewrites the references.

Keys are `<area>/<tenant id>/<name>` (ML artifacts have no tenant: `<area>/<name>`), so a
bank's files can be removed together (the demo reset)."""

import contextlib
import hashlib
import os
import shutil
import tempfile
from collections.abc import Iterator
from functools import lru_cache
from pathlib import Path
from typing import BinaryIO

from openclear.config import get_settings

_S3_PREFIX = "s3://"
_CHUNK = 1024 * 1024

# Each kind of file and the setting naming its local directory.
AREAS = {
    "check_images": "check_image_storage_dir",
    "tenant_assets": "tenant_asset_storage_dir",
    "bulk_uploads": "bulk_upload_storage_dir",
    "data_exports": "data_export_storage_dir",
    "ml_artifacts": "ml_artifact_dir",
    "audit_archives": "audit_archive_storage_dir",
}


class LocalStorage:
    """Files under each area's directory. References are absolute-or-relative paths, as
    the settings give them."""

    def _path(self, area: str, key: str) -> Path:
        path = Path(getattr(get_settings(), AREAS[area])) / key
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def put(self, area: str, key: str, data: bytes, *, read_only: bool = False) -> str:
        path = self._path(area, key)
        if path.exists() and not os.access(path, os.W_OK):
            path.chmod(0o644)  # replacing a file kept read-only (a logo re-upload)
        path.write_bytes(data)
        if read_only:
            with contextlib.suppress(OSError):
                path.chmod(0o444)
        return str(path)

    def put_file(self, area: str, key: str, source: Path, *, read_only: bool = False) -> str:
        """Moves a finished local file (a data export built on disk) into place."""
        path = self._path(area, key)
        if Path(source).resolve() != path.resolve():
            shutil.move(str(source), path)
        if read_only:
            with contextlib.suppress(OSError):
                path.chmod(0o444)
        return str(path)

    def put_stream(self, area: str, key: str, stream: BinaryIO, *, read_only: bool = False) -> tuple[str, int, bytes]:
        path = self._path(area, key)
        digest, size = hashlib.sha256(), 0
        with path.open("wb") as out:
            while chunk := stream.read(_CHUNK):
                digest.update(chunk)
                size += len(chunk)
                out.write(chunk)
        if read_only:
            with contextlib.suppress(OSError):
                path.chmod(0o444)
        return str(path), size, digest.digest()

    def open(self, ref: str) -> BinaryIO:
        return open(ref, "rb")  # noqa: SIM115 -- the caller closes it

    @contextlib.contextmanager
    def local_path(self, ref: str) -> Iterator[Path]:
        yield Path(ref)

    def size(self, ref: str) -> int:
        return Path(ref).stat().st_size

    def delete(self, ref: str) -> None:
        with contextlib.suppress(FileNotFoundError):
            Path(ref).unlink()

    def delete_tenant(self, area: str, tenant_id) -> None:
        shutil.rmtree(Path(getattr(get_settings(), AREAS[area])) / str(tenant_id), ignore_errors=True)


class S3Storage:
    """An S3-compatible bucket. `s3_endpoint_url` points it at MinIO, R2 or another
    provider; credentials come from `s3_access_key_id`/`s3_secret_access_key` or, when
    those are unset, boto3's usual sources (environment, instance role)."""

    def __init__(self) -> None:
        try:
            import boto3
        except ImportError as exc:  # pragma: no cover -- depends on the install
            raise RuntimeError("storage_backend is 's3' but boto3 isn't installed: pip install 'openclear[s3]'") from exc
        settings = get_settings()
        if not settings.s3_bucket:
            raise RuntimeError("storage_backend is 's3' but s3_bucket isn't set")
        self.bucket = settings.s3_bucket
        self.prefix = settings.s3_prefix.strip("/")
        self.client = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint_url or None,
            region_name=settings.s3_region or None,
            aws_access_key_id=settings.s3_access_key_id or None,
            aws_secret_access_key=settings.s3_secret_access_key or None,
        )

    def _key(self, area: str, key: str) -> str:
        return "/".join(part for part in (self.prefix, area, key) if part)

    def _ref(self, object_key: str) -> str:
        return f"{_S3_PREFIX}{self.bucket}/{object_key}"

    @staticmethod
    def _split(ref: str) -> tuple[str, str]:
        bucket, _, key = ref[len(_S3_PREFIX):].partition("/")
        return bucket, key

    def put(self, area: str, key: str, data: bytes, *, read_only: bool = False) -> str:
        object_key = self._key(area, key)
        self.client.put_object(Bucket=self.bucket, Key=object_key, Body=data)
        return self._ref(object_key)

    def put_file(self, area: str, key: str, source: Path, *, read_only: bool = False) -> str:
        object_key = self._key(area, key)
        self.client.upload_file(str(source), self.bucket, object_key)
        Path(source).unlink(missing_ok=True)
        return self._ref(object_key)

    def put_stream(self, area: str, key: str, stream: BinaryIO, *, read_only: bool = False) -> tuple[str, int, bytes]:
        # Hashed on the way through to a temporary file, then uploaded in parts by boto3,
        # so neither step holds the file in memory.
        with tempfile.NamedTemporaryFile(delete=False) as spool:
            digest, size = hashlib.sha256(), 0
            while chunk := stream.read(_CHUNK):
                digest.update(chunk)
                size += len(chunk)
                spool.write(chunk)
        return self.put_file(area, key, Path(spool.name)), size, digest.digest()

    def open(self, ref: str) -> BinaryIO:
        bucket, key = self._split(ref)
        return self.client.get_object(Bucket=bucket, Key=key)["Body"]

    @contextlib.contextmanager
    def local_path(self, ref: str) -> Iterator[Path]:
        """Downloads the object to a temporary file, for what needs a real file (an mmap
        of a cash letter, a zip opened by path)."""
        bucket, key = self._split(ref)
        handle, name = tempfile.mkstemp(suffix=Path(key).suffix)
        os.close(handle)
        try:
            self.client.download_file(bucket, key, name)
            yield Path(name)
        finally:
            Path(name).unlink(missing_ok=True)

    def size(self, ref: str) -> int:
        bucket, key = self._split(ref)
        return int(self.client.head_object(Bucket=bucket, Key=key)["ContentLength"])

    def delete(self, ref: str) -> None:
        bucket, key = self._split(ref)
        self.client.delete_object(Bucket=bucket, Key=key)

    def delete_tenant(self, area: str, tenant_id) -> None:
        prefix = self._key(area, f"{tenant_id}/")
        paginator = self.client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            keys = [{"Key": item["Key"]} for item in page.get("Contents", [])]
            if keys:
                self.client.delete_objects(Bucket=self.bucket, Delete={"Objects": keys})


_local = LocalStorage()


@lru_cache(maxsize=4)
def _s3_for(bucket: str, endpoint: str, prefix: str) -> S3Storage:
    return S3Storage()


def _s3() -> S3Storage:
    settings = get_settings()
    return _s3_for(settings.s3_bucket, settings.s3_endpoint_url, settings.s3_prefix)


def backend() -> LocalStorage | S3Storage:
    """Where new files go: the backend named by `storage_backend`, "local" and "s3" built
    in, others from plugins (plugins/__init__.py)."""
    name = get_settings().storage_backend
    if name == "local":
        return _local
    if name == "s3":
        return _s3()
    from openclear import plugins

    return plugins.lookup("storage_backends", name, what="storage backend")()


def for_ref(ref: str) -> LocalStorage | S3Storage:
    """Where an existing file is, from its stored reference."""
    return _s3() if is_object_ref(ref) else _local


def is_object_ref(ref: str) -> bool:
    return ref.startswith(_S3_PREFIX)


# --- The calls the rest of the app makes ----------------------------------------------------


def put(area: str, key: str, data: bytes, *, read_only: bool = False) -> str:
    return backend().put(area, key, data, read_only=read_only)


def put_file(area: str, key: str, source: Path, *, read_only: bool = False) -> str:
    return backend().put_file(area, key, source, read_only=read_only)


def put_stream(area: str, key: str, stream: BinaryIO, *, read_only: bool = False) -> tuple[str, int, bytes]:
    """Stores a file-like object in pieces. Returns (reference, size, SHA-256 digest)."""
    return backend().put_stream(area, key, stream, read_only=read_only)


def read(ref: str) -> bytes:
    with for_ref(ref).open(ref) as handle:
        return handle.read()


def open_ref(ref: str) -> BinaryIO:
    return for_ref(ref).open(ref)


def iter_chunks(ref: str) -> Iterator[bytes]:
    """The file in 1 MB pieces, for a streamed download or a hash."""
    with for_ref(ref).open(ref) as handle:
        while chunk := handle.read(_CHUNK):
            yield chunk


def sha256(ref: str) -> bytes:
    digest = hashlib.sha256()
    for chunk in iter_chunks(ref):
        digest.update(chunk)
    return digest.digest()


@contextlib.contextmanager
def local_path(ref: str) -> Iterator[Path]:
    with for_ref(ref).local_path(ref) as path:
        yield path


def size(ref: str) -> int:
    return for_ref(ref).size(ref)


def delete(ref: str) -> None:
    for_ref(ref).delete(ref)


def delete_tenant(area: str, tenant_id) -> None:
    """Removes a bank's files in one area, wherever they are."""
    _local.delete_tenant(area, tenant_id)
    if get_settings().storage_backend == "s3":
        _s3().delete_tenant(area, tenant_id)
