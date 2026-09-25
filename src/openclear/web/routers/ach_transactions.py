# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import uuid
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from openclear.bulk_import.nacha import NachaParseError, parse_nacha_file
from openclear.bulk_import.tabular import TabularParseError, parse_tabular_file
from openclear.db.tenancy import TenantContext
from openclear.db.session import get_db
from openclear.domain.ach_transaction import AchTransaction, AchTransactionType
from openclear.domain.bulk_upload_file import BulkUploadKind
from openclear.networks.ach.bulk_import import ingest_ach_rows, ingest_nacha_entries
from openclear.networks.ach.ingestion import AchTransactionSubmission, ingest_ach_transaction
from openclear.repositories.ach_transaction_repo import AchTransactionRepository
from openclear.services import account_service, audit_log_service, bulk_upload_file_service, bulk_upload_reversal_service
from openclear.web.deps import WebNotFound, render_template, require_web_permission
from openclear.web.pagination import paginate
from openclear.web.form_errors import friendly_error
from openclear.web.security import verify_csrf

router = APIRouter(prefix="/ui/ach/transactions", tags=["web-ach-transactions"])


@router.get("")
def list_transactions(
    request: Request, page: int = 1, db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("ach_transaction:read")),
) -> HTMLResponse:
    repo = AchTransactionRepository(db, ctx.tenant_id, ctx.customer_id)
    page_obj = paginate(page=page, count_fn=repo.count, list_fn=lambda **kw: repo.list(order_by=AchTransaction.created_at.desc(), **kw))
    return render_template(request, "ach/transactions_list.html", ctx=ctx, page_obj=page_obj)


@router.get("/new")
def new_transaction_form(
    request: Request,
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("ach_transaction:write")),
) -> HTMLResponse:
    accounts = account_service.list_accounts(db, ctx.tenant_id, customer_id=ctx.customer_id)
    return render_template(request, "ach/transaction_form.html", ctx=ctx, accounts=accounts)


@router.post("")
def create_transaction(
    request: Request,
    account_id: uuid.UUID = Form(...),
    originator_id: str = Form(...),
    originator_name: str = Form(...),
    receiver_id: str = Form(""),
    amount: str = Form(...),
    transaction_type: AchTransactionType = Form(...),
    sec_code: str = Form(...),
    trace_number: str = Form(...),
    effective_date: str = Form(...),
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("ach_transaction:write")),
    _csrf: None = Depends(verify_csrf),
) -> HTMLResponse:
    try:
        parsed_amount = Decimal(amount)
        parsed_date = date.fromisoformat(effective_date)
    except (InvalidOperation, ValueError):
        accounts = account_service.list_accounts(db, ctx.tenant_id, customer_id=ctx.customer_id)
        return render_template(
            request, "ach/transaction_form.html", ctx=ctx, accounts=accounts, error="Invalid amount or date.", status_code=422
        )

    try:
        txn = ingest_ach_transaction(
            db,
            ctx.tenant_id,
            AchTransactionSubmission(
                account_id=account_id,
                originator_id=originator_id,
                originator_name=originator_name,
                receiver_id=receiver_id or None,
                amount=parsed_amount,
                transaction_type=transaction_type,
                sec_code=sec_code.upper(),
                trace_number=trace_number,
                effective_date=parsed_date,
            ),
            scoped_customer_id=ctx.customer_id,
        )
    except Exception as exc:  # noqa: BLE001 — surface e.g. an out-of-scope account to the form
        db.rollback()
        accounts = account_service.list_accounts(db, ctx.tenant_id, customer_id=ctx.customer_id)
        return render_template(
            request, "ach/transaction_form.html", ctx=ctx, accounts=accounts, error=friendly_error(exc, action="Could not submit transaction", duplicate="A transaction with that trace number already exists."), status_code=422
        )
    audit_log_service.record_action(
        db,
        ctx.tenant_id,
        actor_user_id=ctx.user_id,
        channel="web",
        action="ach_transaction.create",
        summary=f"Submitted ACH {transaction_type.value} of {parsed_amount} from {originator_name}",
        resource_type="ach_transaction",
        resource_id=txn.id,
    )
    db.commit()

    flash = "Transaction matched." if txn.match_status.value == "matched" else "Transaction created an exception — see the Exceptions queue."
    return RedirectResponse(f"/ui/ach/transactions/{txn.id}?flash={quote(flash)}", status_code=303)


# NOTE: /bulk must be registered before /{transaction_id} below — FastAPI matches path
# patterns in registration order, and "bulk" would otherwise be captured as
# transaction_id and rejected as an invalid UUID before this route is ever tried.


@router.get("/bulk")
def bulk_upload_form(
    request: Request, ctx: TenantContext = Depends(require_web_permission("ach_transaction:write"))
) -> HTMLResponse:
    return render_template(request, "ach/transaction_bulk_upload.html", ctx=ctx)


@router.post("/bulk")
async def bulk_upload_transactions(
    request: Request,
    upload_file: UploadFile,
    format: Literal["tabular", "nacha"] = Form(...),
    create_missing_accounts: bool = Form(False),
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("ach_transaction:write")),
    _csrf: None = Depends(verify_csrf),
) -> HTMLResponse:
    content = await upload_file.read()

    # Recorded before parsing (and committed on its own) so a rejected/malformed file is
    # still captured for audit purposes, not just successful uploads — see
    # services/bulk_upload_file_service.py.
    upload_record = bulk_upload_file_service.record_uploaded_file(
        db,
        ctx.tenant_id,
        kind=BulkUploadKind.ACH_TRANSACTIONS,
        filename=upload_file.filename or "upload.csv",
        content_type=upload_file.content_type,
        data=content,
        uploaded_by_user_id=ctx.user_id,
        customer_id=ctx.customer_id,
    )
    db.commit()

    if format == "nacha":
        try:
            entries = parse_nacha_file(content)
        except NachaParseError as exc:
            return render_template(
                request,
                "ach/transaction_bulk_upload.html",
                ctx=ctx,
                nacha_error=str(exc),
                status_code=422,
                upload_record=upload_record,
            )
        results = ingest_nacha_entries(
            db, ctx.tenant_id, entries, auto_create_accounts=create_missing_accounts, scoped_customer_id=ctx.customer_id
        )
    else:
        try:
            rows = parse_tabular_file(upload_file.filename or "upload.csv", content)
        except TabularParseError as exc:
            return render_template(
                request,
                "ach/transaction_bulk_upload.html",
                ctx=ctx,
                tabular_error=str(exc),
                status_code=422,
                upload_record=upload_record,
            )
        if not rows:
            return render_template(
                request,
                "ach/transaction_bulk_upload.html",
                ctx=ctx,
                tabular_error="That file has no data rows.",
                status_code=422,
                upload_record=upload_record,
            )
        results = ingest_ach_rows(
            db, ctx.tenant_id, rows, auto_create_accounts=create_missing_accounts, scoped_customer_id=ctx.customer_id
        )

    for r in results:
        if not r.success:
            continue
        audit_log_service.record_action(
            db,
            ctx.tenant_id,
            actor_user_id=ctx.user_id,
            channel="web",
            action="ach_transaction.create",
            summary=f"ACH transaction created via bulk upload ({r.row_label})",
            resource_type="ach_transaction",
            resource_id=r.created_id,
        )
        bulk_upload_reversal_service.track_created_record(
            db, ctx.tenant_id, upload_record.id, resource_type="ach_transaction", resource_id=r.created_id, row_label=r.row_label
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
        back_url="/ui/ach/transactions",
        back_label="Back to ACH transactions",
    )


@router.get("/{transaction_id}")
def transaction_detail(
    request: Request,
    transaction_id: uuid.UUID,
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("ach_transaction:read")),
) -> HTMLResponse:
    txn = AchTransactionRepository(db, ctx.tenant_id, ctx.customer_id).get(transaction_id)
    if txn is None:
        raise WebNotFound()
    return render_template(request, "ach/transaction_detail.html", ctx=ctx, txn=txn)
