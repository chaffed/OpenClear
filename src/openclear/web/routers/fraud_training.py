# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import uuid
from datetime import date
from decimal import Decimal, InvalidOperation
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from openclear.bulk_import.tabular import TabularParseError, parse_tabular_file
from openclear.db.session import get_db
from openclear.db.tenancy import TenantContext
from openclear.domain.ach_transaction import AchTransactionType
from openclear.domain.bulk_upload_file import BulkUploadKind
from openclear.domain.exception_item import ExceptionItem, ExceptionItemSource
from openclear.services import account_service, ach_return_reason_service, audit_log_service, bulk_upload_file_service
from openclear.services.fraud_training_service import (
    AchFraudRawInput,
    CheckFraudRawInput,
    FraudTrainingError,
    retract_fraud_example,
    submit_ach_fraud_example,
    submit_ach_fraud_examples_from_rows,
    submit_check_fraud_example,
    submit_check_fraud_examples_from_rows,
)
from openclear.web.deps import render_template, require_web_permission
from openclear.web.security import verify_csrf
from openclear.web.redirects import see_other

router = APIRouter(prefix="/ui/ml-training/fraud-examples", tags=["web-fraud-training"])


def _parse_optional_uuid(raw: str) -> uuid.UUID | None:
    return uuid.UUID(raw) if raw else None


def _recent_examples(db: Session, ctx: TenantContext) -> list[ExceptionItem]:
    stmt = (
        select(ExceptionItem)
        .where(
            ExceptionItem.tenant_id == ctx.tenant_id,
            ExceptionItem.source == ExceptionItemSource.TRAINING_BACKFILL,
        )
        .order_by(ExceptionItem.created_at.desc())
        .limit(50)
    )
    if ctx.customer_id is not None:
        stmt = stmt.where(ExceptionItem.customer_id == ctx.customer_id)
    return list(db.execute(stmt).scalars().all())


@router.get("")
def fraud_examples_home(
    request: Request,
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("ml_training_example:write")),
) -> HTMLResponse:
    accounts = account_service.list_accounts(db, ctx.tenant_id, customer_id=ctx.customer_id)
    ach_return_reasons = ach_return_reason_service.list_ach_return_reasons(db, ctx.tenant_id, active_only=True)
    return render_template(
        request,
        "fraud_training/home.html",
        ctx=ctx,
        accounts=accounts,
        ach_return_reasons=ach_return_reasons,
        examples=_recent_examples(db, ctx),
    )


@router.post("/check")
def submit_check(
    request: Request,
    paid_item_id: str = Form(""),
    account_id: str = Form(""),
    check_number: str = Form(""),
    presented_amount: str = Form(""),
    presented_date: str = Form(""),
    reason_code: str = Form(...),
    notes: str = Form(""),
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("ml_training_example:write")),
    _csrf: None = Depends(verify_csrf),
) -> RedirectResponse:
    try:
        existing_id = _parse_optional_uuid(paid_item_id)
        new_item = None
        if existing_id is None:
            new_item = CheckFraudRawInput(
                account_id=uuid.UUID(account_id),
                check_number=check_number,
                presented_amount=Decimal(presented_amount),
                presented_date=date.fromisoformat(presented_date),
            )
        exception_item = submit_check_fraud_example(
            db,
            ctx.tenant_id,
            ctx,
            existing_paid_item_id=existing_id,
            new_item=new_item,
            reason_code=reason_code,
            notes=notes or None,
        )
    except (FraudTrainingError, ValueError, InvalidOperation) as exc:
        db.rollback()
        return see_other(f"/ui/ml-training/fraud-examples?error={quote(str(exc))}")

    audit_log_service.record_action(
        db,
        ctx.tenant_id,
        actor_user_id=ctx.user_id,
        channel="web",
        action="fraud_training_example.create",
        summary=f"Submitted a known-fraud check training example ({reason_code})",
        resource_type="exception_item",
        resource_id=exception_item.id,
    )
    db.commit()
    return see_other("/ui/ml-training/fraud-examples?flash=Fraud+training+example+submitted.")


@router.post("/ach")
def submit_ach(
    request: Request,
    ach_transaction_id: str = Form(""),
    account_id: str = Form(""),
    originator_id: str = Form(""),
    originator_name: str = Form(""),
    receiver_id: str = Form(""),
    amount: str = Form(""),
    transaction_type: str = Form(""),
    sec_code: str = Form(""),
    trace_number: str = Form(""),
    effective_date: str = Form(""),
    ach_return_reason_id: str = Form(...),
    notes: str = Form(""),
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("ml_training_example:write")),
    _csrf: None = Depends(verify_csrf),
) -> RedirectResponse:
    try:
        existing_id = _parse_optional_uuid(ach_transaction_id)
        new_item = None
        if existing_id is None:
            new_item = AchFraudRawInput(
                account_id=uuid.UUID(account_id),
                originator_id=originator_id,
                originator_name=originator_name,
                receiver_id=receiver_id or None,
                amount=Decimal(amount),
                transaction_type=AchTransactionType(transaction_type),
                sec_code=sec_code.upper(),
                trace_number=trace_number,
                effective_date=date.fromisoformat(effective_date),
            )
        exception_item = submit_ach_fraud_example(
            db,
            ctx.tenant_id,
            ctx,
            existing_ach_transaction_id=existing_id,
            new_item=new_item,
            ach_return_reason_id=uuid.UUID(ach_return_reason_id),
            notes=notes or None,
        )
    except (FraudTrainingError, ValueError, InvalidOperation) as exc:
        db.rollback()
        return see_other(f"/ui/ml-training/fraud-examples?error={quote(str(exc))}")

    audit_log_service.record_action(
        db,
        ctx.tenant_id,
        actor_user_id=ctx.user_id,
        channel="web",
        action="fraud_training_example.create",
        summary="Submitted a known-fraud ACH training example",
        resource_type="exception_item",
        resource_id=exception_item.id,
    )
    db.commit()
    return see_other("/ui/ml-training/fraud-examples?flash=Fraud+training+example+submitted.")


@router.get("/check/bulk")
def check_bulk_upload_form(
    request: Request, ctx: TenantContext = Depends(require_web_permission("ml_training_example:write"))
) -> HTMLResponse:
    return render_template(request, "fraud_training/check_bulk_upload.html", ctx=ctx)


@router.post("/check/bulk")
async def check_bulk_upload(
    request: Request,
    upload_file: UploadFile,
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("ml_training_example:write")),
    _csrf: None = Depends(verify_csrf),
) -> HTMLResponse:
    content = await upload_file.read()
    upload_record = bulk_upload_file_service.record_uploaded_file(
        db,
        ctx.tenant_id,
        kind=BulkUploadKind.FRAUD_TRAINING_EXAMPLES,
        filename=upload_file.filename or "upload.csv",
        content_type=upload_file.content_type,
        data=content,
        uploaded_by_user_id=ctx.user_id,
        customer_id=ctx.customer_id,
    )
    db.commit()

    try:
        rows = parse_tabular_file(upload_file.filename or "upload.csv", content)
    except TabularParseError as exc:
        return render_template(
            request, "fraud_training/check_bulk_upload.html", ctx=ctx, error=str(exc), status_code=422, upload_record=upload_record
        )
    if not rows:
        return render_template(
            request,
            "fraud_training/check_bulk_upload.html",
            ctx=ctx,
            error="That file has no data rows.",
            status_code=422,
            upload_record=upload_record,
        )

    results = submit_check_fraud_examples_from_rows(db, ctx.tenant_id, ctx, rows)
    for r in results:
        if not r.success:
            continue
        audit_log_service.record_action(
            db,
            ctx.tenant_id,
            actor_user_id=ctx.user_id,
            channel="web",
            action="fraud_training_example.create",
            summary=f"Known-fraud check training example created via bulk upload ({r.row_label})",
            resource_type="exception_item",
            resource_id=r.created_id,
        )
    bulk_upload_file_service.set_result_counts(
        db, upload_record, succeeded_count=sum(r.success for r in results), failed_count=sum(not r.success for r in results)
    )
    db.commit()
    return render_template(
        request,
        "bulk_result.html",
        ctx=ctx,
        results=results,
        upload_record=upload_record,
        back_url="/ui/ml-training/fraud-examples",
        back_label="Back to fraud training examples",
    )


@router.get("/ach/bulk")
def ach_bulk_upload_form(
    request: Request, ctx: TenantContext = Depends(require_web_permission("ml_training_example:write"))
) -> HTMLResponse:
    return render_template(request, "fraud_training/ach_bulk_upload.html", ctx=ctx)


@router.post("/ach/bulk")
async def ach_bulk_upload(
    request: Request,
    upload_file: UploadFile,
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("ml_training_example:write")),
    _csrf: None = Depends(verify_csrf),
) -> HTMLResponse:
    content = await upload_file.read()
    upload_record = bulk_upload_file_service.record_uploaded_file(
        db,
        ctx.tenant_id,
        kind=BulkUploadKind.FRAUD_TRAINING_EXAMPLES,
        filename=upload_file.filename or "upload.csv",
        content_type=upload_file.content_type,
        data=content,
        uploaded_by_user_id=ctx.user_id,
        customer_id=ctx.customer_id,
    )
    db.commit()

    try:
        rows = parse_tabular_file(upload_file.filename or "upload.csv", content)
    except TabularParseError as exc:
        return render_template(
            request, "fraud_training/ach_bulk_upload.html", ctx=ctx, error=str(exc), status_code=422, upload_record=upload_record
        )
    if not rows:
        return render_template(
            request,
            "fraud_training/ach_bulk_upload.html",
            ctx=ctx,
            error="That file has no data rows.",
            status_code=422,
            upload_record=upload_record,
        )

    results = submit_ach_fraud_examples_from_rows(db, ctx.tenant_id, ctx, rows)
    for r in results:
        if not r.success:
            continue
        audit_log_service.record_action(
            db,
            ctx.tenant_id,
            actor_user_id=ctx.user_id,
            channel="web",
            action="fraud_training_example.create",
            summary=f"Known-fraud ACH training example created via bulk upload ({r.row_label})",
            resource_type="exception_item",
            resource_id=r.created_id,
        )
    bulk_upload_file_service.set_result_counts(
        db, upload_record, succeeded_count=sum(r.success for r in results), failed_count=sum(not r.success for r in results)
    )
    db.commit()
    return render_template(
        request,
        "bulk_result.html",
        ctx=ctx,
        results=results,
        upload_record=upload_record,
        back_url="/ui/ml-training/fraud-examples",
        back_label="Back to fraud training examples",
    )


@router.post("/{exception_id}/retract")
def retract(
    exception_id: uuid.UUID,
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("ml_training_example:write")),
    _csrf: None = Depends(verify_csrf),
) -> RedirectResponse:
    try:
        exception_item = retract_fraud_example(db, ctx.tenant_id, ctx, exception_id)
    except FraudTrainingError as exc:
        db.rollback()
        return see_other(f"/ui/ml-training/fraud-examples?error={quote(str(exc))}")
    if exception_item is None:
        db.commit()
        return see_other(f"/ui/ml-training/fraud-examples?error={quote('Not found.')}")

    audit_log_service.record_action(
        db,
        ctx.tenant_id,
        actor_user_id=ctx.user_id,
        channel="web",
        action="fraud_training_example.retract",
        summary="Retracted a known-fraud training example",
        resource_type="exception_item",
        resource_id=exception_item.id,
    )
    db.commit()
    return see_other("/ui/ml-training/fraud-examples?flash=Training+example+retracted.")
