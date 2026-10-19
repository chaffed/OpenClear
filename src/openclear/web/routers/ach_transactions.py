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

from openclear.db.tenancy import TenantContext
from openclear.db.session import get_db
from openclear.domain.ach_transaction import AchTransactionType
from openclear.domain.bulk_upload_file import BulkUploadKind
from openclear.networks.ach.ingestion import AchTransactionSubmission, ingest_ach_transaction
from openclear.repositories.ach_transaction_repo import AchTransactionRepository
from openclear.services import account_service, audit_log_service
from openclear.web.bulk_import import start_import
from openclear.web.deps import WebNotFound, render_template, require_web_permission
from openclear.web.form_errors import friendly_error
from openclear.web.security import verify_csrf
from openclear.web.redirects import see_other

router = APIRouter(prefix="/ui/ach/transactions", tags=["web-ach-transactions"])


@router.get("")
def list_transactions(
    request: Request, page: int = 1, db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("ach_transaction:read")),
):
    from openclear.web import list_search

    search = list_search.parse(request)
    repo = AchTransactionRepository(db, ctx.tenant_id, ctx.customer_id)
    result = list_search.page_or_export(
        request=request, db=db, ctx=ctx, search=search, spec=list_search.ach_transactions_spec(), page=page,
        list_name="ach_transactions", count=lambda where: repo.count(where=where), fetch=lambda **kw: repo.list(**kw),
    )
    if not hasattr(result, "items"):
        return result
    return render_template(request, "ach/transactions_list.html", ctx=ctx, page_obj=result, search=search)


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
    return see_other(f"/ui/ach/transactions/{txn.id}?flash={quote(flash)}")


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
    return start_import(
        request, db, ctx, upload_file, kind=BulkUploadKind.ACH_TRANSACTIONS, file_format=format,
        form_template="ach/transaction_bulk_upload.html", default_filename="upload.csv",
        error_field="nacha_error" if format == "nacha" else "tabular_error",
        options={"create_missing_accounts": create_missing_accounts},
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
