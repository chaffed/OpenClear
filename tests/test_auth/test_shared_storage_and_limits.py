# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""What several instances share (SCALE_PLAN.md S6): files in S3-compatible storage, and
rate-limit counters in Redis. S3 runs against moto's in-process S3; Redis against
fakeredis, and against a real server too when OPENCLEAR_TEST_REDIS_URL is set."""

import io
import os
import uuid

import pytest

from openclear import storage
from openclear.config import get_settings
from openclear.web import rate_limit

boto3 = pytest.importorskip("boto3")
moto = pytest.importorskip("moto")


@pytest.fixture
def s3(monkeypatch):
    with moto.mock_aws():
        boto3.client("s3", region_name="us-east-1").create_bucket(Bucket="openclear-test")
        settings = get_settings()
        for name, value in {
            "storage_backend": "s3", "s3_bucket": "openclear-test", "s3_prefix": "bank-files", "s3_region": "us-east-1",
            "s3_access_key_id": "testing", "s3_secret_access_key": "testing",
        }.items():
            monkeypatch.setattr(settings, name, value)
        storage._s3_for.cache_clear()
        yield boto3.client("s3", region_name="us-east-1")
        storage._s3_for.cache_clear()


def _keys(client) -> set[str]:
    return {item["Key"] for item in client.list_objects_v2(Bucket="openclear-test").get("Contents", [])}


def test_files_round_trip_through_object_storage(s3):
    tenant = uuid.uuid4()
    ref = storage.put("check_images", f"{tenant}/a_front.png", b"front")
    assert ref == f"s3://openclear-test/bank-files/check_images/{tenant}/a_front.png"
    assert storage.read(ref) == b"front" and storage.size(ref) == 5

    streamed, size, digest = storage.put_stream("bulk_uploads", f"{tenant}/u.csv", io.BytesIO(b"x" * 3_000_000))
    assert size == 3_000_000 and storage.sha256(streamed) == digest
    with storage.local_path(streamed) as path:  # a real file, for an mmap or a zip
        assert path.read_bytes()[:3] == b"xxx"
    assert not path.exists()  # the download is cleaned up

    storage.delete_tenant("check_images", tenant)
    assert _keys(s3) == {f"bank-files/bulk_uploads/{tenant}/u.csv"}


def test_local_references_stay_readable_after_switching_to_s3(s3, tmp_path):
    old = tmp_path / "logo.png"
    old.write_bytes(b"from before the switch")
    assert storage.read(str(old)) == b"from before the switch"


def test_a_bulk_upload_imports_from_object_storage(s3, client, db_session, tenant_factory):
    from openclear.domain.bulk_upload_file import BulkUploadFile, BulkUploadKind, BulkUploadStatus
    from openclear.services import bulk_import_service, bulk_upload_file_service
    from openclear.workers import jobs

    tenant, account, users = tenant_factory.make(slug="s3-bulk")
    data = f"account_number,check_number,presented_amount,presented_date\n{account.account_number},51,10.00,2026-03-02\n".encode()
    record = bulk_import_service.receive_upload(
        db_session, tenant.id, kind=BulkUploadKind.PAID_ITEMS, file_format="tabular", filename="c.csv", content_type=None,
        uploaded_by_user_id=users["admin"].id, customer_id=None, stream=io.BytesIO(data),
    )
    assert record.storage_path.startswith("s3://")
    jobs.drain(db_session.get_bind())
    db_session.expire_all()
    record = db_session.get(BulkUploadFile, record.id)
    assert (record.status, record.succeeded_count) == (BulkUploadStatus.COMPLETED, 1)
    assert bulk_upload_file_service.verify_uploaded_file(db_session, tenant.id, record.id) is True


def test_model_files_live_in_object_storage(s3, db_session, tenant_factory, monkeypatch):
    from openclear.domain.tenant import MlModelSource
    from openclear.ml.registry import ArtifactStore
    from openclear.ml.train import train_model
    from tests.test_ml.test_bank_model_choice import _bank

    monkeypatch.setattr(get_settings(), "ml_retrain_cooldown_seconds", 0)
    tenant, _account, _users = _bank(tenant_factory, db_session, "s3-mdl", MlModelSource.PRIVATE)
    row = train_model(db_session, "check", tenant_id=tenant.id).model_row
    assert row.artifact_path.startswith("s3://")
    assert ArtifactStore().load_model(row) is not None


# --- Rate limits shared through Redis ----------------------------------------------------


def _limiter_on(client) -> rate_limit.RedisRateLimiter:
    return rate_limit.RedisRateLimiter(client)


def test_redis_limiter_counts_across_instances():
    fakeredis = pytest.importorskip("fakeredis")
    server = fakeredis.FakeServer()
    one, two = _limiter_on(fakeredis.FakeRedis(server=server)), _limiter_on(fakeredis.FakeRedis(server=server))

    assert [one.allow("1.2.3.4", "global", limit=3, window_seconds=60) for _ in range(2)] == [True, True]
    assert two.allow("1.2.3.4", "global", limit=3, window_seconds=60) is True   # the other instance sees both hits
    assert one.allow("1.2.3.4", "global", limit=3, window_seconds=60) is False
    assert two.allow("5.6.7.8", "global", limit=3, window_seconds=60) is True   # other clients unaffected
    assert two.allow("1.2.3.4", "markdown_preview", limit=3, window_seconds=60) is True  # and other buckets


def test_redis_limiter_fails_open_when_redis_is_down(caplog):
    redis = pytest.importorskip("redis")
    down = _limiter_on(redis.Redis(host="127.0.0.1", port=1, socket_connect_timeout=0.2, socket_timeout=0.2))
    assert all(down.allow("1.2.3.4", "global", limit=1, window_seconds=60) for _ in range(3))
    assert "can't be reached" in caplog.text


def test_the_app_uses_the_configured_backend(client, monkeypatch):
    fakeredis = pytest.importorskip("fakeredis")
    shared = _limiter_on(fakeredis.FakeRedis())
    monkeypatch.setattr(get_settings(), "rate_limit_backend", "redis")
    monkeypatch.setattr(get_settings(), "rate_limit_per_minute", 2)
    monkeypatch.setattr(rate_limit, "_redis_limiter", lambda url: shared)

    statuses = [client.get("/ui/login").status_code for _ in range(3)]
    assert statuses == [200, 200, 429]
    assert not rate_limit.limiter._hits  # nothing counted in-process


@pytest.mark.skipif(not os.environ.get("OPENCLEAR_TEST_REDIS_URL"), reason="set OPENCLEAR_TEST_REDIS_URL to test against a real Redis")
def test_redis_limiter_against_a_real_server():
    import redis

    client = redis.Redis.from_url(os.environ["OPENCLEAR_TEST_REDIS_URL"])
    key = uuid.uuid4().hex
    limiter = _limiter_on(client)
    assert [limiter.allow(key, "t", limit=2, window_seconds=1) for _ in range(3)] == [True, True, False]
    import time

    time.sleep(1.1)
    assert limiter.allow(key, "t", limit=2, window_seconds=1) is True


def test_pool_settings_reach_the_engine(monkeypatch):
    pytest.importorskip("psycopg")
    from openclear.config import Settings
    from openclear.db.session import create_db_engine

    settings = Settings(
        environment="development", database_url="postgresql+psycopg://u:p@127.0.0.1:1/x",
        db_pool_size=7, db_max_overflow=3, db_pool_timeout_seconds=9, db_pool_recycle_seconds=600,
    )
    pool = create_db_engine(settings).pool  # no connection is made until first use
    assert (pool.size(), pool._max_overflow, pool._timeout, pool._recycle) == (7, 3, 9, 600)


def test_existing_local_files_move_to_object_storage(s3, db_session, session_factory, tenant_factory, monkeypatch, capsys):
    """scripts/move_files_to_object_storage.py, end to end on one bank's logo."""
    import importlib.util
    from pathlib import Path

    from openclear.domain.tenant import Tenant
    from openclear.web.branding_storage import save_tenant_asset

    tenant, _account, _users = tenant_factory.make(slug="s3-move")
    monkeypatch.setattr(get_settings(), "storage_backend", "local")
    tenant.logo_path = save_tenant_asset(tenant.id, "logo", "image/png", b"PNG-bytes")
    db_session.commit()
    local = tenant.logo_path
    monkeypatch.setattr(get_settings(), "storage_backend", "s3")

    spec = importlib.util.spec_from_file_location("move_files", Path("scripts/move_files_to_object_storage.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr("openclear.db.session.get_session_factory", lambda: session_factory)
    monkeypatch.setattr("sys.argv", ["move", "--delete-local"])
    module.main()

    db_session.expire_all()
    moved = db_session.get(Tenant, tenant.id).logo_path
    assert moved == f"s3://openclear-test/bank-files/tenant_assets/{tenant.id}/logo.png"
    assert storage.read(moved) == b"PNG-bytes" and not Path(local).exists()
    module.main()  # a second run finds nothing to do
    assert "0 files moved" in capsys.readouterr().out
