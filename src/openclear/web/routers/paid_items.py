# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import uuid
from datetime import date
from decimal import Decimal, InvalidOperation
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from openclear.bulk_import.tabular import TabularParseError, parse_tabular_file
from openclear.db.session import get_db
from openclear.db.tenancy import TenantContext
from openclear.domain.bulk_upload_file import BulkUploadKind
from openclear.domain.paid_item import PaidItem
from openclear.networks.check.bulk_import import ingest_paid_item_tabular_rows
from openclear.networks.check.ingestion import PaidItemSubmission, ingest_paid_item
from openclear.repositories.paid_item_repo import PaidItemRepository
from openclear.services import account_service, audit_log_service, bulk_upload_file_service, bulk_upload_reversal_service
from openclear.web.deps import WebNotFound, render_template, require_web_permission
from openclear.web.pagination import paginate
from openclear.web.form_errors import friendly_error
from openclear.web.security import verify_csrf

router = APIRouter(prefix="/ui/paid-items", tags=["web-paid-items"])


@router.get("")
def list_paid_items(
    request: Request, page: int = 1, db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("paid_item:read")),
) -> HTMLResponse:
    repo = PaidItemRepository(db, ctx.tenant_id, ctx.customer_id)
    page_obj = paginate(page=page, count_fn=repo.count, list_fn=lambda **kw: repo.list(order_by=PaidItem.created_at.desc(), **kw))
    return render_template(request, "paid_items/list.html", ctx=ctx, page_obj=page_obj)


@router.get("/new")
def new_paid_item_form(
    request: Request,
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("paid_item:write")),
) -> HTMLResponse:
    accounts = account_service.list_accounts(db, ctx.tenant_id, customer_id=ctx.customer_id)
    return render_template(request, "paid_items/form.html", ctx=ctx, accounts=accounts)


@router.post("")
def create_paid_item(
    request: Request,
    account_id: uuid.UUID = Form(...),
    check_number: str = Form(...),
    presented_amount: str = Form(...),
    presented_date: str = Form(...),
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("paid_item:write")),
    _csrf: None = Depends(verify_csrf),
) -> HTMLResponse:
    try:
        parsed_amount = Decimal(presented_amount)
        parsed_date = date.fromisoformat(presented_date)
    except (InvalidOperation, ValueError):
        accounts = account_service.list_accounts(db, ctx.tenant_id, customer_id=ctx.customer_id)
        return render_template(
            request, "paid_items/form.html", ctx=ctx, accounts=accounts, error="Invalid amount or date.", status_code=422
        )

    try:
        item = ingest_paid_item(
            db,
            ctx.tenant_id,
            PaidItemSubmission(
                account_id=account_id, check_number=check_number, presented_amount=parsed_amount, presented_date=parsed_date
            ),
            scoped_customer_id=ctx.customer_id,
        )
    except Exception as exc:  # noqa: BLE001 — surface e.g. an out-of-scope account to the form
        db.rollback()
        accounts = account_service.list_accounts(db, ctx.tenant_id, customer_id=ctx.customer_id)
        return render_template(
            request, "paid_items/form.html", ctx=ctx, accounts=accounts, error=friendly_error(exc, action="Could not submit paid item"), status_code=422
        )
    audit_log_service.record_action(
        db,
        ctx.tenant_id,
        actor_user_id=ctx.user_id,
        channel="web",
        action="paid_item.create",
        summary=f"Presented check #{item.check_number} for {item.presented_amount}",
        resource_type="paid_item",
        resource_id=item.id,
    )
    db.commit()

    flash = "Paid item matched." if item.match_status.value == "matched" else "Paid item created an exception — see the Exceptions queue."
    return RedirectResponse(f"/ui/paid-items/{item.id}?flash={quote(flash)}", status_code=303)


# NOTE: /bulk must be registered before /{item_id} — FastAPI matches path patterns in
# registration order, and "bulk" would otherwise be captured as item_id and rejected as
# an invalid UUID before this route is ever tried. Same class of pitfall as /bulk vs
# /{item_id} elsewhere in this codebase.


@router.get("/bulk")
def bulk_upload_form(
    request: Request, ctx: TenantContext = Depends(require_web_permission("paid_item:write"))
) -> HTMLResponse:
    return render_template(request, "paid_items/bulk_upload.html", ctx=ctx)


@router.post("/bulk")
async def bulk_upload_paid_items(
    request: Request,
    upload_file: UploadFile,
    create_missing_accounts: bool = Form(False),
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("paid_item:write")),
    _csrf: None = Depends(verify_csrf),
) -> HTMLResponse:
    """Checks presented for payment as plain delimited data, no image attached — see
    networks/check/bulk_import.py::ingest_paid_item_tabular_rows. For an image-bearing
    presentment (a real cash letter), see /ui/check-images/bulk instead."""
    content = await upload_file.read()

    upload_record = bulk_upload_file_service.record_uploaded_file(
        db,
        ctx.tenant_id,
        kind=BulkUploadKind.PAID_ITEMS,
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
            request, "paid_items/bulk_upload.html", ctx=ctx, error=str(exc), status_code=422, upload_record=upload_record
        )
    if not rows:
        return render_template(
            request, "paid_items/bulk_upload.html", ctx=ctx, error="That file has no data rows.", status_code=422,
            upload_record=upload_record,
        )

    results = ingest_paid_item_tabular_rows(
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
            action="paid_item.create",
            summary=f"Paid item created via bulk upload ({r.row_label})",
            resource_type="paid_item",
            resource_id=r.created_id,
        )
        bulk_upload_reversal_service.track_created_record(
            db, ctx.tenant_id, upload_record.id, resource_type="paid_item", resource_id=r.created_id, row_label=r.row_label
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
        back_url="/ui/paid-items",
        back_label="Back to paid items",
    )


@router.get("/{item_id}")
def paid_item_detail(
    request: Request,
    item_id: uuid.UUID,
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("paid_item:read")),
) -> HTMLResponse:
    item = PaidItemRepository(db, ctx.tenant_id, ctx.customer_id).get(item_id)
    if item is None:
        raise WebNotFound()
    return render_template(request, "paid_items/detail.html", ctx=ctx, item=item)
