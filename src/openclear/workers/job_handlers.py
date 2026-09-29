# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""What each kind of background job does (see workers/jobs.py). Each handler gets a
session already bound to the job's bank, and the job row for its payload. A handler that
raises is retried with back-off; the services these call already record their own
user-visible failure (a failed OCR, a failed export) where that matters."""

import uuid

from sqlalchemy.orm import Session

from openclear.domain.job import Job
from openclear.domain.tenant import Tenant
from openclear.workers.jobs import handler

OCR_CHECK_IMAGE = "check_image.ocr"
RUN_DATA_EXPORT = "data_export.run"
SCAN_BANK_DROPBOX = "dropbox.scan_bank"
RETRAIN_MODEL_SLOT = "ml.retrain_slot"


@handler(OCR_CHECK_IMAGE)
def _ocr_check_image(session: Session, job: Job) -> None:
    from openclear.networks.check.ocr_processing import process_check_image_ocr

    process_check_image_ocr(session, uuid.UUID(job.payload["check_image_id"]), tenant_id=job.tenant_id)


@handler(RUN_DATA_EXPORT)
def _run_data_export(session: Session, job: Job) -> None:
    from openclear.services import data_export_service

    data_export_service.run_export_job(session.get_bind(), uuid.UUID(job.payload["export_job_id"]), job.tenant_id)


@handler(SCAN_BANK_DROPBOX)
def _scan_bank_dropbox(session: Session, job: Job) -> None:
    import logging

    from openclear.services.dropbox_import_service import scan_and_import_tenant

    tenant = session.get(Tenant, job.tenant_id)
    if tenant is None:
        return
    results = scan_and_import_tenant(session, tenant)
    logging.getLogger(__name__).info(
        "Dropbox scan for %s: %d imported, %d failed, %d duplicates skipped", tenant.slug,
        sum(r.outcome == "imported" for r in results), sum(r.outcome == "failed" for r in results),
        sum(r.outcome == "duplicate" for r in results),
    )


@handler(RETRAIN_MODEL_SLOT)
def _retrain_model_slot(session: Session, job: Job) -> None:
    from openclear.workers.tasks import _train_and_log

    payload = job.payload
    _train_and_log(
        session,
        payload["network_code"],
        customer_id=uuid.UUID(payload["customer_id"]) if payload.get("customer_id") else None,
        tenant_id=uuid.UUID(payload["tenant_id"]) if payload.get("tenant_id") else None,
        label=payload.get("label", "shared"),
        force_activate_reason=payload.get("force_activate_reason"),
    )
