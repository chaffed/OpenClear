# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Housekeeping (SCALE_PLAN.md S7): old operational rows go, recent ones and anything still
in flight stay, and a bank's business records are untouched."""

import io
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import func, select

from openclear import storage
from openclear.config import get_settings
from openclear.domain.bulk_upload_file import BulkUploadFile, BulkUploadKind, BulkUploadRowResult
from openclear.domain.data_export_job import DataExportJob, DataExportJobStatus
from openclear.domain.job import Job, JobStatus
from openclear.domain.notification import Notification, NotificationChannel, NotificationStatus, NotificationType
from openclear.domain.paid_item import PaidItem
from openclear.services import bulk_import_service, housekeeping_service
from openclear.workers import jobs


def _ago(days: int) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=days)


def _count(db_session, model, *where) -> int:
    return db_session.scalar(select(func.count()).select_from(model).where(*where))


def test_old_operational_rows_go_and_the_rest_stay(db_session, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="housekeeping")
    admin = users["admin"]

    # An upload a year and a half old: its row results go, the upload and what it created stay.
    data = f"account_number,check_number,presented_amount,presented_date\n{account.account_number},61,10.00,2026-03-02\n".encode()
    upload = bulk_import_service.receive_upload(
        db_session, tenant.id, kind=BulkUploadKind.PAID_ITEMS, file_format="tabular", filename="old.csv", content_type=None,
        uploaded_by_user_id=admin.id, customer_id=None, stream=io.BytesIO(data),
    )
    jobs.drain(db_session.get_bind())
    db_session.expire_all()
    upload = db_session.get(BulkUploadFile, upload.id)
    upload.completed_at = _ago(500)

    db_session.add_all([
        Job(kind="t", status=JobStatus.SUCCEEDED, finished_at=_ago(40), payload={}),   # goes
        Job(kind="t", status=JobStatus.FAILED, finished_at=_ago(40), payload={}),      # goes
        Job(kind="t", status=JobStatus.SUCCEEDED, finished_at=_ago(5), payload={}),    # recent: stays
        Job(kind="t", status=JobStatus.QUEUED, payload={}),                            # in flight: stays
    ])
    for status, age in ((NotificationStatus.SENT, 200), (NotificationStatus.PENDING, 200), (NotificationStatus.SENT, 10)):
        db_session.add(Notification(
            tenant_id=tenant.id, recipient_user_id=admin.id, notification_type=NotificationType.EXCEPTION_CREATED,
            channel=NotificationChannel.EMAIL, destination=admin.email, subject="s", body="b", status=status,
            created_at=_ago(age),
        ))
    db_session.commit()

    # An export archive past its 30 days, and a fresh one.
    old_ref = storage.put("data_exports", f"{tenant.id}/old.zip", b"zip")
    new_ref = storage.put("data_exports", f"{tenant.id}/new.zip", b"zip")
    old_export = DataExportJob(tenant_id=tenant.id, requested_by_user_id=admin.id, status=DataExportJobStatus.COMPLETED,
                               archive_path=old_ref, completed_at=_ago(31))
    new_export = DataExportJob(tenant_id=tenant.id, requested_by_user_id=admin.id, status=DataExportJobStatus.COMPLETED,
                               archive_path=new_ref, completed_at=_ago(1))
    db_session.add_all([old_export, new_export])
    db_session.commit()
    jobs_before = _count(db_session, Job)

    result = housekeeping_service.run(db_session)

    assert result.deleted["jobs"] == 2
    assert _count(db_session, Job) == jobs_before - 2
    assert _count(db_session, Job, Job.status == JobStatus.QUEUED) == 1
    assert _count(db_session, Notification, Notification.subject == "s") == 2  # the pending and the recent one
    assert _count(db_session, BulkUploadRowResult, BulkUploadRowResult.bulk_upload_file_id == upload.id) == 0
    assert db_session.get(BulkUploadFile, upload.id).succeeded_count == 1
    assert _count(db_session, PaidItem, PaidItem.tenant_id == tenant.id) == 1  # business records untouched

    db_session.expire_all()
    assert (db_session.get(DataExportJob, old_export.id).status, db_session.get(DataExportJob, old_export.id).archive_path) == (
        DataExportJobStatus.EXPIRED, None)
    assert not Path(old_ref).exists() and Path(new_ref).exists()
    assert db_session.get(DataExportJob, new_export.id).status == DataExportJobStatus.COMPLETED


def test_zero_keeps_everything(db_session, monkeypatch):
    for name in ("retention_finished_jobs_days", "retention_notifications_days", "retention_bulk_row_results_days",
                 "retention_data_exports_days", "idempotency_key_hours", "security_event_retention_days"):
        monkeypatch.setattr(get_settings(), name, 0)
    db_session.add(Job(kind="t", status=JobStatus.SUCCEEDED, finished_at=_ago(4000), payload={}))
    db_session.commit()

    assert housekeeping_service.run(db_session).deleted == {}
    assert _count(db_session, Job) == 1
