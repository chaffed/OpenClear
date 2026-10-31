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
- "azure": an Azure Blob Storage container, the same way. Needs `pip install
  openclear[azure]` (and azure-identity to sign in with a managed identity).

The database stores a reference to each file, never the bytes. A local file's
reference is its path, exactly as before S6; an object's is `s3://bucket/key` or
`azblob://container/name`. Reads go
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
_AZURE_PREFIX = "azblob://"
_CHUNK = 1024 * 1024

# Each kind of file and the setting naming its local directory.
AREAS = {
    "check_images": "check_image_storage_dir",
    "tenant_assets": "tenant_asset_storage_dir",
    "bulk_uploads": "bulk_upload_storage_dir",
    "data_exports": "data_export_storage_dir",
    "ml_artifacts": "ml_artifact_dir",
    "audit_archives": "audit_archive_storage_dir",
    "decision_files": "decision_file_storage_dir",
    "backup_manifests": "backup_manifest_storage_dir",
}


class UnsafeKey(ValueError):
    """A key that would land outside its area: refused, whatever the backend."""


def safe_key(key: str) -> str:
    """The key, if it's a relative path of plain names: no `..` or `.`, no empty segments, no
    leading slash, backslash or control character. Every write checks it (CODE_SCANNING_PLAN.md
    CS0): a key built from an uploaded file's name once reached the disk unchecked."""
    parts = key.split("/")
    if (not key or key.startswith("/") or "\\" in key or any(ord(ch) < 32 for ch in key)
            or any(part in ("", ".", "..") for part in parts)):
        raise UnsafeKey(f"Refused a storage key that isn't a plain relative path: {key!r}")
    return key


def safe_name(name: str | None, default: str = "file") -> str:
    """An uploaded file's name, fit to end a storage key: its last part only, without
    control characters, at most 200 characters."""
    name = "".join(ch for ch in (name or "") if ord(ch) >= 32).replace("\\", "/").rsplit("/", 1)[-1].strip()
    return (name if name not in ("", ".", "..") else default)[-200:]


class LocalStorage:
    """Files under each area's directory. References are absolute-or-relative paths, as
    the settings give them."""

    def _path(self, area: str, key: str) -> Path:
        root = Path(getattr(get_settings(), AREAS[area]))
        path = root / safe_key(key)
        # The stored reference stays as the settings give it (relative or not); the check uses
        # the resolved paths, so a symlink or anything safe_key missed can't leave the area.
        if not path.resolve().is_relative_to(root.resolve()):
            raise UnsafeKey(f"Refused a storage key outside its area: {key!r}")
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


class AzureBlobStorage:
    """An Azure Blob Storage container. Signs in with `azure_storage_connection_string`, or
    with the account URL and whatever credentials azure-identity finds (a managed identity,
    the environment, the Azure CLI)."""

    def __init__(self, service=None) -> None:
        settings = get_settings()
        if not settings.azure_storage_container:
            raise RuntimeError("storage_backend is 'azure' but azure_storage_container isn't set")
        self.container = settings.azure_storage_container
        self.prefix = settings.azure_storage_prefix.strip("/")
        self.service = service or self._service(settings)

    @staticmethod
    def _service(settings):
        try:
            from azure.storage.blob import BlobServiceClient
        except ImportError as exc:  # pragma: no cover -- depends on the install
            raise RuntimeError("storage_backend is 'azure' but azure-storage-blob isn't installed: pip install 'openclear[azure]'") from exc
        if settings.azure_storage_connection_string:
            return BlobServiceClient.from_connection_string(settings.azure_storage_connection_string)
        if not settings.azure_storage_account_url:
            raise RuntimeError("storage_backend is 'azure' but neither a connection string nor an account URL is set")
        try:
            from azure.identity import DefaultAzureCredential
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("Signing in to Azure without a connection string needs azure-identity: pip install azure-identity") from exc
        return BlobServiceClient(account_url=settings.azure_storage_account_url, credential=DefaultAzureCredential())

    def _name(self, area: str, key: str) -> str:
        return "/".join(part for part in (self.prefix, area, key) if part)

    def _ref(self, name: str) -> str:
        return f"{_AZURE_PREFIX}{self.container}/{name}"

    @staticmethod
    def _split(ref: str) -> tuple[str, str]:
        container, _, name = ref[len(_AZURE_PREFIX):].partition("/")
        return container, name

    def _blob(self, container: str, name: str):
        return self.service.get_blob_client(container=container, blob=name)

    def put(self, area: str, key: str, data: bytes, *, read_only: bool = False) -> str:
        name = self._name(area, key)
        self._blob(self.container, name).upload_blob(data, overwrite=True)
        return self._ref(name)

    def put_file(self, area: str, key: str, source: Path, *, read_only: bool = False) -> str:
        name = self._name(area, key)
        with open(source, "rb") as handle:
            # The SDK uploads a stream in blocks, so a large file isn't held in memory.
            self._blob(self.container, name).upload_blob(handle, overwrite=True)
        Path(source).unlink(missing_ok=True)
        return self._ref(name)

    def put_stream(self, area: str, key: str, stream: BinaryIO, *, read_only: bool = False) -> tuple[str, int, bytes]:
        with tempfile.NamedTemporaryFile(delete=False) as spool:
            digest, size = hashlib.sha256(), 0
            while chunk := stream.read(_CHUNK):
                digest.update(chunk)
                size += len(chunk)
                spool.write(chunk)
        return self.put_file(area, key, Path(spool.name)), size, digest.digest()

    def open(self, ref: str) -> BinaryIO:
        container, name = self._split(ref)
        return _ChunkReader(self._blob(container, name).download_blob().chunks())

    @contextlib.contextmanager
    def local_path(self, ref: str) -> Iterator[Path]:
        container, name = self._split(ref)
        handle, temp = tempfile.mkstemp(suffix=Path(name).suffix)
        try:
            with os.fdopen(handle, "wb") as out:
                self._blob(container, name).download_blob().readinto(out)
            yield Path(temp)
        finally:
            Path(temp).unlink(missing_ok=True)

    def size(self, ref: str) -> int:
        container, name = self._split(ref)
        return int(self._blob(container, name).get_blob_properties().size)

    def delete(self, ref: str) -> None:
        container, name = self._split(ref)
        self._blob(container, name).delete_blob()

    def delete_tenant(self, area: str, tenant_id) -> None:
        container = self.service.get_container_client(self.container)
        for blob in container.list_blobs(name_starts_with=self._name(area, f"{tenant_id}/")):
            container.delete_blob(blob.name)


class _ChunkReader:
    """A file-like reader over a download's chunks, so a blob is streamed, not read whole."""

    def __init__(self, chunks) -> None:
        self._chunks, self._buffer = iter(chunks), b""

    def read(self, size: int = -1) -> bytes:
        while size < 0 or len(self._buffer) < size:
            chunk = next(self._chunks, None)
            if chunk is None:
                break
            self._buffer += chunk
        if size < 0:
            data, self._buffer = self._buffer, b""
        else:
            data, self._buffer = self._buffer[:size], self._buffer[size:]
        return data

    def close(self) -> None:
        self._chunks, self._buffer = iter(()), b""

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> None:
        self.close()


_local = LocalStorage()


@lru_cache(maxsize=4)
def _s3_for(bucket: str, endpoint: str, prefix: str) -> S3Storage:
    return S3Storage()


def _s3() -> S3Storage:
    settings = get_settings()
    return _s3_for(settings.s3_bucket, settings.s3_endpoint_url, settings.s3_prefix)


@lru_cache(maxsize=4)
def _azure_for(container: str, connection: str, account_url: str, prefix: str) -> AzureBlobStorage:
    return AzureBlobStorage()


def _azure() -> AzureBlobStorage:
    settings = get_settings()
    return _azure_for(settings.azure_storage_container, settings.azure_storage_connection_string,
                      settings.azure_storage_account_url, settings.azure_storage_prefix)


def backend() -> LocalStorage | S3Storage | AzureBlobStorage:
    """Where new files go: the backend named by `storage_backend`, "local" and "s3" built
    in, others from plugins (plugins/__init__.py)."""
    name = get_settings().storage_backend
    if name == "local":
        return _local
    if name == "s3":
        return _s3()
    if name == "azure":
        return _azure()
    from openclear import plugins

    return plugins.lookup("storage_backends", name, what="storage backend")()


def for_ref(ref: str) -> LocalStorage | S3Storage | AzureBlobStorage:
    """Where an existing file is, from its stored reference."""
    if ref.startswith(_S3_PREFIX):
        return _s3()
    if ref.startswith(_AZURE_PREFIX):
        return _azure()
    return _local


def is_object_ref(ref: str) -> bool:
    return ref.startswith((_S3_PREFIX, _AZURE_PREFIX))


# --- The calls the rest of the app makes ----------------------------------------------------


def put(area: str, key: str, data: bytes, *, read_only: bool = False) -> str:
    return backend().put(area, safe_key(key), data, read_only=read_only)


def put_file(area: str, key: str, source: Path, *, read_only: bool = False) -> str:
    return backend().put_file(area, safe_key(key), source, read_only=read_only)


def put_stream(area: str, key: str, stream: BinaryIO, *, read_only: bool = False) -> tuple[str, int, bytes]:
    """Stores a file-like object in pieces. Returns (reference, size, SHA-256 digest)."""
    return backend().put_stream(area, safe_key(key), stream, read_only=read_only)


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


def read_key(area: str, key: str) -> bytes | None:
    """A file by its area and key, or None when there isn't one or the store can't be reached:
    for the backup manifest the host's job leaves (CONTINUITY_PLAN.md BC-D2), which nothing in
    OpenClear wrote, so there's no stored reference to it."""
    store = backend()
    try:
        if isinstance(store, LocalStorage):
            path = Path(getattr(get_settings(), AREAS[area])) / safe_key(key)
            return path.read_bytes() if path.is_file() else None
        if isinstance(store, S3Storage):
            return store.open(store._ref(store._key(area, key))).read()
        return store.open(store._ref(store._name(area, key))).read()
    except Exception:  # noqa: BLE001 -- not there, or unreachable: either way there's nothing to read
        return None


def delete_tenant(area: str, tenant_id) -> None:
    """Removes a bank's files in one area, wherever they are."""
    _local.delete_tenant(area, tenant_id)
    if get_settings().storage_backend == "s3":
        _s3().delete_tenant(area, tenant_id)
    if get_settings().storage_backend == "azure":
        _azure().delete_tenant(area, tenant_id)
