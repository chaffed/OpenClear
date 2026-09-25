# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from openclear.auth.deps import require_permission
from openclear.db.session import get_db
from openclear.db.tenancy import TenantContext
from openclear.schemas.common import BulkRowResultOut, BulkSubmitResponse
from openclear.schemas.fraud_training import AchFraudExampleCreate, CheckFraudExampleCreate, FraudExampleRead
from openclear.services import audit_log_service
from openclear.services.fraud_training_service import (
    AchFraudRawInput,
    CheckFraudRawInput,
    FraudTrainingError,
    retract_fraud_example,
    submit_ach_fraud_example,
    submit_check_fraud_example,
)

router = APIRouter(prefix="/ml-training/fraud-examples", tags=["fraud-training"])


def _check_raw_input(payload: CheckFraudExampleCreate) -> CheckFraudRawInput:
    if None in (payload.account_id, payload.check_number, payload.presented_amount, payload.presented_date):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "account_id, check_number, presented_amount, and presented_date are all required when paid_item_id is omitted",
        )
    return CheckFraudRawInput(
        account_id=payload.account_id,
        check_number=payload.check_number,
        presented_amount=payload.presented_amount,
        presented_date=payload.presented_date,
    )


def _ach_raw_input(payload: AchFraudExampleCreate) -> AchFraudRawInput:
    if None in (
        payload.account_id,
        payload.originator_id,
        payload.originator_name,
        payload.amount,
        payload.transaction_type,
        payload.sec_code,
        payload.trace_number,
        payload.effective_date,
    ):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "account_id, originator_id, originator_name, amount, transaction_type, sec_code, trace_number, and "
            "effective_date are all required when ach_transaction_id is omitted",
        )
    return AchFraudRawInput(
        account_id=payload.account_id,
        originator_id=payload.originator_id,
        originator_name=payload.originator_name,
        receiver_id=payload.receiver_id,
        amount=payload.amount,
        transaction_type=payload.transaction_type,
        sec_code=payload.sec_code,
        trace_number=payload.trace_number,
        effective_date=payload.effective_date,
    )


@router.post("/check", response_model=FraudExampleRead, status_code=status.HTTP_201_CREATED)
def create_check_fraud_example(
    payload: CheckFraudExampleCreate,
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_permission("ml_training_example:write")),
) -> FraudExampleRead:
    new_item = None if payload.paid_item_id else _check_raw_input(payload)
    try:
        exception_item = submit_check_fraud_example(
            db,
            ctx.tenant_id,
            ctx,
            existing_paid_item_id=payload.paid_item_id,
            new_item=new_item,
            reason_code=payload.reason_code,
            notes=payload.notes,
        )
    except FraudTrainingError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from None
    audit_log_service.record_action(
        db,
        ctx.tenant_id,
        actor_user_id=ctx.user_id,
        channel="api",
        action="fraud_training_example.create",
        summary=f"Submitted a known-fraud check training example ({payload.reason_code})",
        resource_type="exception_item",
        resource_id=exception_item.id,
    )
    db.commit()
    return FraudExampleRead.model_validate(exception_item)


@router.post("/ach", response_model=FraudExampleRead, status_code=status.HTTP_201_CREATED)
def create_ach_fraud_example(
    payload: AchFraudExampleCreate,
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_permission("ml_training_example:write")),
) -> FraudExampleRead:
    new_item = None if payload.ach_transaction_id else _ach_raw_input(payload)
    try:
        exception_item = submit_ach_fraud_example(
            db,
            ctx.tenant_id,
            ctx,
            existing_ach_transaction_id=payload.ach_transaction_id,
            new_item=new_item,
            ach_return_reason_id=payload.ach_return_reason_id,
            notes=payload.notes,
        )
    except FraudTrainingError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from None
    audit_log_service.record_action(
        db,
        ctx.tenant_id,
        actor_user_id=ctx.user_id,
        channel="api",
        action="fraud_training_example.create",
        summary="Submitted a known-fraud ACH training example",
        resource_type="exception_item",
        resource_id=exception_item.id,
    )
    db.commit()
    return FraudExampleRead.model_validate(exception_item)


@router.post("/check/bulk", response_model=BulkSubmitResponse)
def create_check_fraud_examples_bulk(
    payload: list[CheckFraudExampleCreate],
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_permission("ml_training_example:write")),
) -> BulkSubmitResponse:
    results: list[BulkRowResultOut] = []
    for index, row in enumerate(payload):
        try:
            new_item = None if row.paid_item_id else _check_raw_input(row)
            exception_item = submit_check_fraud_example(
                db,
                ctx.tenant_id,
                ctx,
                existing_paid_item_id=row.paid_item_id,
                new_item=new_item,
                reason_code=row.reason_code,
                notes=row.notes,
            )
            db.commit()
            results.append(BulkRowResultOut(index=index, success=True, id=str(exception_item.id)))
        except HTTPException as exc:
            db.rollback()
            results.append(BulkRowResultOut(index=index, success=False, error=str(exc.detail)))
        except Exception as exc:  # noqa: BLE001 — isolate row failures in a bulk submission
            db.rollback()
            results.append(BulkRowResultOut(index=index, success=False, error=str(exc)))
    for r in results:
        if not r.success:
            continue
        audit_log_service.record_action(
            db,
            ctx.tenant_id,
            actor_user_id=ctx.user_id,
            channel="api",
            action="fraud_training_example.create",
            summary=f"Known-fraud check training example created via bulk API (row {r.index})",
            resource_type="exception_item",
            resource_id=uuid.UUID(r.id),
        )
    db.commit()
    return BulkSubmitResponse(
        total=len(results), succeeded=sum(r.success for r in results), failed=sum(not r.success for r in results), results=results
    )


@router.post("/ach/bulk", response_model=BulkSubmitResponse)
def create_ach_fraud_examples_bulk(
    payload: list[AchFraudExampleCreate],
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_permission("ml_training_example:write")),
) -> BulkSubmitResponse:
    results: list[BulkRowResultOut] = []
    for index, row in enumerate(payload):
        try:
            new_item = None if row.ach_transaction_id else _ach_raw_input(row)
            exception_item = submit_ach_fraud_example(
                db,
                ctx.tenant_id,
                ctx,
                existing_ach_transaction_id=row.ach_transaction_id,
                new_item=new_item,
                ach_return_reason_id=row.ach_return_reason_id,
                notes=row.notes,
            )
            db.commit()
            results.append(BulkRowResultOut(index=index, success=True, id=str(exception_item.id)))
        except HTTPException as exc:
            db.rollback()
            results.append(BulkRowResultOut(index=index, success=False, error=str(exc.detail)))
        except Exception as exc:  # noqa: BLE001 — isolate row failures in a bulk submission
            db.rollback()
            results.append(BulkRowResultOut(index=index, success=False, error=str(exc)))
    for r in results:
        if not r.success:
            continue
        audit_log_service.record_action(
            db,
            ctx.tenant_id,
            actor_user_id=ctx.user_id,
            channel="api",
            action="fraud_training_example.create",
            summary=f"Known-fraud ACH training example created via bulk API (row {r.index})",
            resource_type="exception_item",
            resource_id=uuid.UUID(r.id),
        )
    db.commit()
    return BulkSubmitResponse(
        total=len(results), succeeded=sum(r.success for r in results), failed=sum(not r.success for r in results), results=results
    )


@router.post("/{exception_id}/retract", response_model=FraudExampleRead)
def retract_fraud_example_api(
    exception_id: uuid.UUID,
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_permission("ml_training_example:write")),
) -> FraudExampleRead:
    try:
        exception_item = retract_fraud_example(db, ctx.tenant_id, ctx, exception_id)
    except FraudTrainingError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from None
    if exception_item is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    audit_log_service.record_action(
        db,
        ctx.tenant_id,
        actor_user_id=ctx.user_id,
        channel="api",
        action="fraud_training_example.retract",
        summary="Retracted a known-fraud training example",
        resource_type="exception_item",
        resource_id=exception_item.id,
    )
    db.commit()
    return FraudExampleRead.model_validate(exception_item)
