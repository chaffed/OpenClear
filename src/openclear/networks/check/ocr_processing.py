# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from openclear.db.tenancy import bind_session_to_tenant
from openclear.domain.check_image import CheckImage, OcrStatus
from openclear.domain.exception_item import ExceptionItem
from openclear.domain.paid_item import PaidItem, PaidItemMatchStatus
from openclear.ml.predict import score_exception
from openclear.networks.check.adapter import CheckAdapter
from openclear.ocr.factory import get_ocr_provider
from openclear.ocr.storage import read_image, save_image

_adapter = CheckAdapter()


def _apply_rls_tenant(session: Session, tenant_id: uuid.UUID) -> None:
    """Postgres RLS: every transaction on this session, including those after a commit,
    runs as this tenant (db/tenancy.py::bind_session_to_tenant). A no-op on SQLite/MSSQL."""
    bind_session_to_tenant(session, tenant_id)


def create_check_image(
    session: Session,
    tenant_id: uuid.UUID,
    *,
    front_bytes: bytes,
    back_bytes: bytes | None,
    paid_item_id: uuid.UUID | None,
    customer_id: uuid.UUID | None = None,
) -> CheckImage:
    check_image = CheckImage(tenant_id=tenant_id, paid_item_id=paid_item_id, customer_id=customer_id, front_image_path="")
    session.add(check_image)
    session.flush()  # assign id, needed for the file name

    check_image.front_image_path = save_image(tenant_id, check_image.id, "front", front_bytes)
    if back_bytes is not None:
        check_image.back_image_path = save_image(tenant_id, check_image.id, "back", back_bytes)

    session.flush()
    return check_image


def process_check_image_ocr(session: Session, check_image_id: uuid.UUID, tenant_id: uuid.UUID | None = None) -> None:
    """Runs OCR and persists the result — intended to be called from a FastAPI
    BackgroundTask so the upload request returns immediately (see api/v1/check_images.py).
    If the image is already linked to a paid_item, re-runs the matching engine afterward:
    a payee mismatch discovered only now (OCR wasn't done at ingestion time) can still
    escalate an already-'matched' paid_item into an exception, or add to an existing one.

    `tenant_id`: pass explicitly when calling from a fresh session (e.g. the background
    task in api/v1/check_images.py) so the very first lookup below succeeds under Postgres
    RLS; omit only when the session already has app.current_tenant_id set for this request."""
    if tenant_id is not None:
        _apply_rls_tenant(session, tenant_id)

    check_image = session.get(CheckImage, check_image_id)
    if check_image is None:
        return
    tenant_id = check_image.tenant_id

    try:
        provider = get_ocr_provider()
        result = provider.extract(read_image(check_image.front_image_path))
        check_image.ocr_extracted_amount = result.extracted_amount
        check_image.ocr_extracted_payee = result.extracted_payee
        check_image.ocr_confidence = result.confidence
        check_image.ocr_raw_result = result.raw_response
        check_image.ocr_provider = provider.name
        check_image.ocr_status = OcrStatus.COMPLETED
    except Exception as exc:  # noqa: BLE001 — OCR failures must not crash the background task
        check_image.ocr_status = OcrStatus.FAILED
        check_image.ocr_raw_result = {"error": str(exc)}
    finally:
        check_image.processed_at = datetime.now(timezone.utc)
        session.commit()

    if check_image.paid_item_id is not None and check_image.ocr_status == OcrStatus.COMPLETED:
        _apply_rls_tenant(session, tenant_id)  # the commit above reset the transaction-local setting
        _reevaluate_paid_item_after_ocr(session, check_image.paid_item_id)


def _reevaluate_paid_item_after_ocr(session: Session, paid_item_id: uuid.UUID) -> None:
    paid_item = session.get(PaidItem, paid_item_id)
    if paid_item is None:
        return

    result = _adapter.evaluate(session, paid_item)
    if result.matched:
        return  # still clean — nothing to escalate

    existing_exception = (
        session.query(ExceptionItem)
        .filter(ExceptionItem.source_item_id == paid_item.id, ExceptionItem.network_code == "check")
        .one_or_none()
    )

    if existing_exception is None:
        paid_item.match_status = PaidItemMatchStatus.EXCEPTION
        exception_item = ExceptionItem(
            tenant_id=paid_item.tenant_id,
            network_code="check",
            customer_id=paid_item.customer_id,
            source_item_id=paid_item.id,
            related_reference_id=result.related_reference_id,
            exception_types=",".join(result.exception_types),
        )
        session.add(exception_item)
        session.flush()
        score_exception(session, exception_item)
    else:
        current_types = set(existing_exception.exception_types.split(","))
        merged_types = current_types | set(result.exception_types)
        if merged_types != current_types:
            existing_exception.exception_types = ",".join(sorted(merged_types))

    session.commit()
