# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Bulk files imported by a background job (SCALE_PLAN.md S4): chunked commits that
resume after a crash, rows isolated by savepoints, one notification summary per upload,
the API's 202 hand-off, and files read without holding them in memory."""

import io
import zipfile

import pytest
from sqlalchemy import func, select

from openclear.bulk_import.signing import verify_signature
from openclear.bulk_import.x937 import item_images, parse_x937_file
from openclear.bulk_import.zip_import import find_image, parse_zip_manifest
from openclear.config import get_settings
from openclear.domain.bulk_upload_file import BulkUploadFile, BulkUploadKind, BulkUploadRowResult, BulkUploadStatus
from openclear.domain.job import Job, JobStatus
from openclear.domain.notification import Notification, NotificationChannel, NotificationType
from openclear.domain.paid_item import PaidItem
from openclear.services import audit_log_service, bulk_import_service, bulk_upload_file_service
from openclear.workers import jobs
from tests.conftest import TenantFactory, login_headers


def _paid_items_csv(account_number: str, count: int, *, bad_row: int | None = None) -> bytes:
    lines = ["account_number,check_number,presented_amount,presented_date"]
    for n in range(count):
        amount = "oops" if n == bad_row else f"{100 + n}.00"
        lines.append(f"{account_number},{7000 + n},{amount},2026-03-02")
    return ("\n".join(lines) + "\n").encode()


def _receive(db_session, tenant, users, data: bytes, *, kind=BulkUploadKind.PAID_ITEMS, file_format="tabular", filename="checks.csv"):
    return bulk_import_service.receive_upload(
        db_session, tenant.id, kind=kind, file_format=file_format, filename=filename, content_type="text/csv",
        uploaded_by_user_id=users["admin"].id, customer_id=None, stream=io.BytesIO(data),
    )


def _fresh(db_session, model, ident):
    db_session.expire_all()
    return db_session.get(model, ident)


def _paid_item_count(db_session, tenant) -> int:
    return db_session.scalar(select(func.count()).select_from(PaidItem).where(PaidItem.tenant_id == tenant.id))


def test_import_runs_as_a_job_in_chunks_with_a_bad_row_isolated(db_session, tenant_factory, monkeypatch):
    monkeypatch.setattr(get_settings(), "bulk_import_chunk_rows", 2)
    tenant, account, users = tenant_factory.make(slug="bulk-job-chunks")
    record = _receive(db_session, tenant, users, _paid_items_csv(account.account_number, 5, bad_row=2))
    assert (record.status, record.total_rows, record.processed_rows) == (BulkUploadStatus.QUEUED, 5, 0)
    assert _paid_item_count(db_session, tenant) == 0  # nothing imported inside the request

    jobs.drain(db_session.get_bind())

    record = _fresh(db_session, BulkUploadFile, record.id)
    assert (record.status, record.processed_rows, record.succeeded_count, record.failed_count) == (BulkUploadStatus.COMPLETED, 5, 4, 1)
    assert _paid_item_count(db_session, tenant) == 4
    failures = bulk_import_service.row_results(db_session, record, failures_only=True)
    assert [(r.row_index, r.row_label) for r in failures] == [(2, "row 4")]
    assert "presented_amount" in failures[0].error
    assert len(bulk_import_service.row_results(db_session, record)) == 5
    # One audit entry per created row, written a chunk at a time, and the chain still verifies.
    entries = audit_log_service.list_entries(db_session, tenant.id)
    assert sum(e.action == "paid_item.create" for e in entries) == 4
    assert audit_log_service.verify_chain(db_session, tenant.id).valid


def test_a_crashed_import_resumes_without_duplicating_rows(db_session, tenant_factory, monkeypatch):
    monkeypatch.setattr(get_settings(), "bulk_import_chunk_rows", 2)
    tenant, account, users = tenant_factory.make(slug="bulk-job-resume")
    record = _receive(db_session, tenant, users, _paid_items_csv(account.account_number, 5))

    real_record_chunk = bulk_import_service._record_chunk
    calls = {"n": 0}

    def crash_on_second_chunk(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("worker died")
        return real_record_chunk(*args, **kwargs)

    monkeypatch.setattr(bulk_import_service, "_record_chunk", crash_on_second_chunk)
    jobs.drain(db_session.get_bind())

    record = _fresh(db_session, BulkUploadFile, record.id)
    assert (record.status, record.processed_rows) == (BulkUploadStatus.PROCESSING, 2)  # the first chunk stuck
    assert _paid_item_count(db_session, tenant) == 2
    job = db_session.execute(select(Job).where(Job.kind == bulk_import_service.IMPORT_JOB_KIND)).scalar_one()
    assert job.status == JobStatus.QUEUED and "worker died" in job.last_error  # to be retried

    monkeypatch.setattr(bulk_import_service, "_record_chunk", real_record_chunk)
    bulk_import_service.run_import(db_session, record.id)  # the retry

    record = _fresh(db_session, BulkUploadFile, record.id)
    assert (record.status, record.processed_rows, record.succeeded_count, record.failed_count) == (BulkUploadStatus.COMPLETED, 5, 5, 0)
    assert _paid_item_count(db_session, tenant) == 5
    assert db_session.scalar(select(func.count()).select_from(BulkUploadRowResult).where(BulkUploadRowResult.bulk_upload_file_id == record.id)) == 5


def test_the_last_failed_attempt_marks_the_upload_failed(db_session, tenant_factory, monkeypatch):
    tenant, account, users = tenant_factory.make(slug="bulk-job-gives-up")
    record = _receive(db_session, tenant, users, _paid_items_csv(account.account_number, 2))
    monkeypatch.setattr(bulk_import_service, "_record_chunk", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("disk full")))

    with pytest.raises(RuntimeError):
        bulk_import_service.run_import(db_session, record.id, last_attempt=True)

    record = _fresh(db_session, BulkUploadFile, record.id)
    assert record.status == BulkUploadStatus.FAILED and "disk full" in record.error


def test_an_upload_whose_job_the_queue_failed_shows_failed(db_session, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="bulk-job-queue-failed")
    record = _receive(db_session, tenant, users, _paid_items_csv(account.account_number, 1))
    job = db_session.execute(select(Job).where(Job.kind == bulk_import_service.IMPORT_JOB_KIND)).scalar_one()
    job.status, job.last_error = JobStatus.FAILED, "stale: worker stopped sending heartbeats"
    db_session.commit()

    bulk_import_service.check_job(db_session, record)
    assert _fresh(db_session, BulkUploadFile, record.id).status == BulkUploadStatus.FAILED


def test_a_malformed_file_is_rejected_whole_and_kept(db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="bulk-job-rejected")
    record = _receive(db_session, tenant, users, b"MZ\x00\x01not a nacha file", kind=BulkUploadKind.ACH_TRANSACTIONS, file_format="nacha", filename="ach.txt")
    assert record.status == BulkUploadStatus.REJECTED and record.error
    assert db_session.scalar(select(func.count()).select_from(Job)) == 0
    assert bulk_upload_file_service.verify_uploaded_file(db_session, tenant.id, record.id) is True


def test_one_summary_notification_per_upload(db_session, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="bulk-job-summary")
    # No issued checks on file, so each presented check is an exception.
    record = _receive(db_session, tenant, users, _paid_items_csv(account.account_number, 3))
    jobs.drain(db_session.get_bind())

    record = _fresh(db_session, BulkUploadFile, record.id)
    assert record.exception_counts == {f":{account.id}": 3}  # by customer (none) and account
    emails = db_session.execute(
        select(Notification).where(
            Notification.tenant_id == tenant.id,
            Notification.notification_type == NotificationType.EXCEPTION_CREATED,
            Notification.channel == NotificationChannel.EMAIL,
        )
    ).scalars().all()
    assert emails, "someone can recommend decisions"
    assert len({n.recipient_user_id for n in emails}) == len(emails)  # one each, not one per exception
    assert all(n.subject == "3 new exceptions ready for review" and "checks.csv" in n.body for n in emails)


def test_a_streamed_upload_is_signed_like_any_other(db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="bulk-job-signed")
    data = b"account_number,name\n9,Payroll\n"
    record = bulk_upload_file_service.record_uploaded_file(
        db_session, tenant.id, kind=BulkUploadKind.ACCOUNTS, filename="a.csv", content_type=None,
        uploaded_by_user_id=users["admin"].id, stream=io.BytesIO(data),
    )
    db_session.commit()
    assert record.size_bytes == len(data)
    assert verify_signature(data, record.signature_hex)  # the same signature sign_file would check
    assert bulk_upload_file_service.verify_uploaded_file(db_session, tenant.id, record.id) is True


def test_lazy_zip_images_match_eager_ones():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("manifest.csv", "account_number,check_number,amount,presented_date,front_image_filename\n1,2,3.00,2026-01-01,a.png\n")
        archive.writestr("images/A.PNG", b"front-bytes")
    content = buffer.getvalue()

    eager_rows, eager_images = parse_zip_manifest(content)
    lazy_rows, lazy_images = parse_zip_manifest(content, lazy_images=True)
    assert eager_rows == lazy_rows
    assert find_image(lazy_images, "a.png") == find_image(eager_images, "a.png") == b"front-bytes"


def test_lazy_x937_images_match_eager_ones():
    from tests.x937_builder import Check, build

    content = build([[[
        Check("1001/", "5001", 15000, images=[b"front-1", b"back-1"]), Check("1001/", "5002", 2500, images=[b"front-2"]),
        Check("1001/", "5003", 100),
    ]]])
    eager = parse_x937_file(content)
    lazy = parse_x937_file(content, lazy_images=True)
    assert [i.check_number for i in eager] == [i.check_number for i in lazy]
    assert lazy[0].front_image_bytes == b"" and lazy[0].front_image_span is not None
    assert [item_images(i, content) for i in lazy] == [(i.front_image_bytes, i.back_image_bytes) for i in eager]


def test_api_bulk_over_the_cap_is_queued_and_pollable(client, db_session, tenant_factory, monkeypatch):
    monkeypatch.setattr(get_settings(), "bulk_api_sync_max_rows", 2)
    tenant, account, users = tenant_factory.make(slug="bulk-api-queued")
    headers = login_headers(client, tenant.slug, users["admin"].email)
    items = [
        {"account_id": str(account.id), "check_number": str(8000 + n), "presented_amount": "12.50", "presented_date": "2026-03-02"}
        for n in range(3)
    ]

    small = client.post("/api/v1/paid-items/bulk", headers=headers, json=items[:2])
    assert small.status_code == 200 and small.json()["succeeded"] == 2  # at the cap: answered inline

    resp = client.post("/api/v1/paid-items/bulk", headers=headers, json=items)
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["total"] == 3 and body["status_url"] == f"/api/v1/bulk-uploads/{body['upload_id']}"

    jobs.drain(db_session.get_bind())  # the import may still be running in the background; finish it
    upload = client.get(body["status_url"], headers=headers).json()
    assert (upload["status"], upload["source"], upload["succeeded"], upload["failed"]) == ("completed", "api", 3, 0)
    results = client.get(f"{body['status_url']}/results?limit=2", headers=headers)
    assert results.headers["X-Total-Count"] == "3" and 'rel="next"' in results.headers["Link"]
    assert [r["index"] for r in results.json()] == [0, 1] and all(r["success"] for r in results.json())

    viewer = login_headers(client, tenant.slug, users["viewer"].email)
    assert client.get(body["status_url"], headers=viewer).status_code == 403


def test_the_upload_page_shows_progress_until_the_import_runs(client, db_session, tenant_factory, monkeypatch):
    monkeypatch.setattr(get_settings(), "worker_mode", "external")  # nothing runs the job yet
    tenant, account, users = tenant_factory.make(slug="bulk-web-progress")
    client.get("/ui/login")
    csrf = client.cookies.get("csrf_token")
    client.post("/ui/login", data={"tenant_slug": tenant.slug, "email": users["admin"].email, "password": TenantFactory.PASSWORD, "csrf_token": csrf, "next": "/ui/"})

    resp = client.post(
        "/ui/paid-items/bulk", data={"csrf_token": csrf},
        files={"upload_file": ("checks.csv", _paid_items_csv(account.account_number, 2), "text/csv")},
    )
    assert resp.status_code == 200 and resp.url.path.startswith("/ui/bulk-uploads/")
    assert "Waiting to import" in resp.text and 'http-equiv="refresh"' in resp.text

    jobs.drain(db_session.get_bind())
    done = client.get(resp.url.path)
    assert "2 of 2 succeeded" in done.text and 'http-equiv="refresh"' not in done.text
