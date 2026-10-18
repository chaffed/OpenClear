# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Azure Blob storage (storage.AzureBlobStorage): against an in-memory stand-in for the
Azure SDK, and against Azurite too when OPENCLEAR_TEST_AZURE_CONNECTION_STRING is set
(docker run -p 10000:10000 mcr.microsoft.com/azure-storage/azurite azurite-blob --blobHost 0.0.0.0)."""

import io
import os
import uuid
from types import SimpleNamespace

import pytest

from openclear import storage
from openclear.config import get_settings


class _Blob:
    def __init__(self, store: dict, container: str, name: str) -> None:
        self.store, self.key = store, (container, name)

    def upload_blob(self, data, overwrite=False):
        assert overwrite
        self.store[self.key] = data if isinstance(data, bytes) else data.read()

    def download_blob(self):
        data = self.store[self.key]
        return SimpleNamespace(
            chunks=lambda: (data[i:i + 1000] for i in range(0, len(data), 1000)),
            readinto=lambda out: out.write(data),
        )

    def get_blob_properties(self):
        return SimpleNamespace(size=len(self.store[self.key]))

    def delete_blob(self):
        del self.store[self.key]


class FakeBlobService:
    def __init__(self) -> None:
        self.store: dict[tuple[str, str], bytes] = {}

    def get_blob_client(self, container, blob):
        return _Blob(self.store, container, blob)

    def get_container_client(self, container):
        store = self.store
        return SimpleNamespace(
            list_blobs=lambda name_starts_with: [SimpleNamespace(name=n) for c, n in list(store) if c == container and n.startswith(name_starts_with)],
            delete_blob=lambda name: store.pop((container, name)),
        )


def _configure(monkeypatch, **extra) -> None:
    settings = get_settings()
    for name, value in {"storage_backend": "azure", "azure_storage_container": "openclear-test", "azure_storage_prefix": "bank-files",
                        **extra}.items():
        monkeypatch.setattr(settings, name, value)


@pytest.fixture
def azure(monkeypatch):
    _configure(monkeypatch)
    service = FakeBlobService()
    backend = storage.AzureBlobStorage(service=service)
    monkeypatch.setattr(storage, "_azure", lambda: backend)
    return service


def _round_trip() -> tuple[uuid.UUID, str]:
    tenant = uuid.uuid4()
    ref = storage.put("check_images", f"{tenant}/a_front.png", b"front")
    assert ref == f"azblob://openclear-test/bank-files/check_images/{tenant}/a_front.png"
    assert storage.read(ref) == b"front" and storage.size(ref) == 5

    streamed, size, digest = storage.put_stream("bulk_uploads", f"{tenant}/u.csv", io.BytesIO(b"x" * 3_000_000))
    assert size == 3_000_000 and storage.sha256(streamed) == digest
    with storage.open_ref(streamed) as handle:
        assert handle.read(3) == b"xxx" and len(handle.read()) == 3_000_000 - 3
    with storage.local_path(streamed) as path:
        assert path.read_bytes()[:3] == b"xxx"
    assert not path.exists()

    storage.delete_tenant("check_images", tenant)
    with pytest.raises(Exception):
        storage.read(ref)
    assert storage.read(streamed)[:1] == b"x"
    return tenant, streamed


def test_files_round_trip_through_azure_blob_storage(azure):
    tenant, streamed = _round_trip()
    assert set(azure.store) == {("openclear-test", f"bank-files/bulk_uploads/{tenant}/u.csv")}
    storage.delete(streamed)
    assert azure.store == {}


def test_local_references_stay_readable_after_switching_to_azure(azure, tmp_path):
    old = tmp_path / "logo.png"
    old.write_bytes(b"from before the switch")
    assert storage.read(str(old)) == b"from before the switch"
    assert not storage.is_object_ref(str(old)) and storage.is_object_ref("azblob://c/k")


def test_a_bulk_upload_imports_from_azure(db_session, tenant_factory, azure):  # azure last: the Postgres db_session rebuilds settings
    from openclear.domain.bulk_upload_file import BulkUploadFile, BulkUploadKind, BulkUploadStatus
    from openclear.services import bulk_import_service
    from openclear.workers import jobs

    tenant, account, users = tenant_factory.make(slug="azure-bulk")
    data = f"account_number,check_number,presented_amount,presented_date\n{account.account_number},51,10.00,2026-03-02\n".encode()
    record = bulk_import_service.receive_upload(
        db_session, tenant.id, kind=BulkUploadKind.PAID_ITEMS, file_format="tabular", filename="c.csv", content_type=None,
        uploaded_by_user_id=users["admin"].id, customer_id=None, stream=io.BytesIO(data),
    )
    assert record.storage_path.startswith("azblob://")
    jobs.drain(db_session.get_bind())
    db_session.expire_all()
    record = db_session.get(BulkUploadFile, record.id)
    assert (record.status, record.succeeded_count) == (BulkUploadStatus.COMPLETED, 1)


@pytest.mark.skipif(not os.environ.get("OPENCLEAR_TEST_AZURE_CONNECTION_STRING"), reason="needs Azurite or a real account")
def test_files_round_trip_through_a_real_azure_service(monkeypatch):
    from azure.storage.blob import BlobServiceClient

    connection = os.environ["OPENCLEAR_TEST_AZURE_CONNECTION_STRING"]
    service = BlobServiceClient.from_connection_string(connection)
    container = service.get_container_client("openclear-test")
    if not container.exists():
        container.create_container()
    _configure(monkeypatch, azure_storage_connection_string=connection)
    storage._azure_for.cache_clear()
    try:
        tenant, streamed = _round_trip()
        storage.delete(streamed)
        assert not list(container.list_blobs(name_starts_with=f"bank-files/bulk_uploads/{tenant}/"))
    finally:
        storage._azure_for.cache_clear()
