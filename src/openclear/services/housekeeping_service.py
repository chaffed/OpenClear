# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Clears out operational rows that grow with every request (SCALE_PLAN.md S7): finished
background jobs, sent notifications, bulk-upload row results, and old data export
archives. Each kind has a retention period in config.py (retention_*_days; 0 keeps it
forever). A bank's business records are never deleted here: how long to keep those is
the bank's own compliance decision (see SCALE_PLAN.md S7). Audit entries are only ever
moved, verified, into signed archive files, and only when audit_archive_after_days is set
(services/audit_archive_service.py).

Deletes run in batches, each committed on its own, so a large backlog doesn't hold one
long transaction. Runs across every bank, so it needs the database owner's view (no bank
bound); workers.tasks.housekeeping_job calls it."""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from openclear import storage
from openclear.config import get_settings
from openclear.domain.bulk_upload_file import BulkUploadFile, BulkUploadRowResult
from openclear.domain.data_export_job import DataExportJob, DataExportJobStatus
from openclear.domain.idempotency_record import IdempotencyRecord
from openclear.domain.sso_connection import SsoAssertionSeen
from openclear.domain.job import Job, JobStatus
from openclear.domain.notification import Notification, NotificationStatus

logger = logging.getLogger(__name__)

_BATCH = 5000


@dataclass
class HousekeepingResult:
    deleted: dict[str, int] = field(default_factory=dict)


def _cutoff(session: Session, days: int) -> datetime | None:
    if days <= 0:
        return None
    return session.execute(select(func.now())).scalar_one() - timedelta(days=days)


def _delete_in_batches(session: Session, model, *conditions) -> int:
    total = 0
    while True:
        ids = session.execute(select(model.id).where(*conditions).limit(_BATCH)).scalars().all()
        if not ids:
            return total
        session.execute(delete(model).where(model.id.in_(ids)))
        session.commit()
        total += len(ids)


def run(session: Session) -> HousekeepingResult:
    settings = get_settings()
    result = HousekeepingResult()

    if cutoff := _cutoff(session, settings.retention_finished_jobs_days):
        result.deleted["jobs"] = _delete_in_batches(
            session, Job, Job.status.in_([JobStatus.SUCCEEDED, JobStatus.FAILED]), Job.finished_at < cutoff
        )

    if settings.idempotency_key_hours > 0:
        cutoff = session.execute(select(func.now())).scalar_one() - timedelta(hours=settings.idempotency_key_hours)
        result.deleted["idempotency_keys"] = _delete_in_batches(session, IdempotencyRecord, IdempotencyRecord.created_at < cutoff)

    # Security events past their retention (services/security_events.py; SIEM_PLAN.md SI-D7),
    # except those a destination that receives them hasn't had yet: one that's been down for
    # longer than the retention period still gets every event when it recovers (SI-D4).
    if cutoff := _cutoff(session, settings.security_event_retention_days):
        from sqlalchemy import exists, or_

        from openclear.domain.security_event import SecurityEvent
        from openclear.domain.security_event_destination import SecurityEventDestination as Destination

        undelivered = exists().where(
            Destination.enabled.is_(True), Destination.cursor < SecurityEvent.id,
            or_(Destination.tenant_id.is_(None), Destination.tenant_id == SecurityEvent.tenant_id))
        result.deleted["security_events"] = _delete_in_batches(session, SecurityEvent, SecurityEvent.occurred_at < cutoff, ~undelivered)

    # Control test results and closed deviations past their retention (CONTROLS_PLAN.md CT2).
    if cutoff := _cutoff(session, settings.control_result_retention_days):
        from openclear.services import control_results_service

        results, deviations = control_results_service.prune(session, older_than=cutoff)
        session.commit()
        if results:
            result.deleted["control_results"] = results
        if deviations:
            result.deleted["control_deviations"] = deviations

    # Client assertions' ids, once the assertions themselves have expired (auth/client_assertion.py):
    # an expired assertion is refused anyway, so its jti needn't be remembered.
    from openclear.domain.api_client import ClientAssertionUse

    now = session.execute(select(func.now())).scalar_one()
    if spent := _delete_in_batches(session, ClientAssertionUse, ClientAssertionUse.expires_at < now - timedelta(minutes=5)):
        result.deleted["client_assertion_ids"] = spent

    # Vendor sandboxes past their lifetime (services/sandbox_service.py).
    from openclear.services import sandbox_service

    if wiped := sandbox_service.expire_due(session):
        result.deleted["sandboxes"] = wiped

    # Used SAML assertions are only kept until they'd have expired anyway (no setting:
    # keeping them longer protects nothing).
    now = session.execute(select(func.now())).scalar_one()
    if expired := _delete_in_batches(session, SsoAssertionSeen, SsoAssertionSeen.expires_at < now):
        result.deleted["sso_assertions"] = expired

    if cutoff := _cutoff(session, settings.retention_notifications_days):
        result.deleted["notifications"] = _delete_in_batches(
            session, Notification, Notification.status != NotificationStatus.PENDING, Notification.created_at < cutoff
        )

    if cutoff := _cutoff(session, settings.retention_bulk_row_results_days):
        # The upload record itself, its file and what it created are kept: only the
        # per-row outcomes go (the upload keeps its counts).
        old_uploads = select(BulkUploadFile.id).where(BulkUploadFile.completed_at < cutoff)
        result.deleted["bulk_row_results"] = _delete_in_batches(
            session, BulkUploadRowResult, BulkUploadRowResult.bulk_upload_file_id.in_(old_uploads)
        )

    if cutoff := _cutoff(session, settings.retention_data_exports_days):
        expired = 0
        for job in session.execute(
            select(DataExportJob).where(DataExportJob.status == DataExportJobStatus.COMPLETED, DataExportJob.completed_at < cutoff)
        ).scalars().all():
            if job.archive_path:
                try:
                    storage.delete(job.archive_path)
                except Exception:  # noqa: BLE001 -- keep going; the next run tries again
                    logger.exception("Couldn't delete expired export archive %s", job.archive_path)
                    continue
            job.status, job.archive_path = DataExportJobStatus.EXPIRED, None
            session.commit()
            expired += 1
        result.deleted["data_export_archives"] = expired

    if settings.audit_archive_after_days > 0:
        from openclear.services import audit_archive_service

        archived = audit_archive_service.archive_all_banks(session, settings.audit_archive_after_days)
        result.deleted["audit_entries_archived"] = sum(archived.values())

    logger.info("Housekeeping: %s", ", ".join(f"{count} {name}" for name, count in result.deleted.items()) or "nothing to do")
    return result
