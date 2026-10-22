# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import uuid
from datetime import date
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, Form, Request, UploadFile
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from openclear.db.session import get_db
from openclear.db.tenancy import TenantContext
from openclear.domain.bulk_upload_file import BulkUploadKind
from openclear.networks.check.ingestion import PaidItemSubmission, ingest_paid_item
from openclear.repositories.paid_item_repo import PaidItemRepository
from openclear.services import account_service, audit_log_service
from openclear.web.bulk_import import start_import
from openclear.web.deps import WebNotFound, render_template, require_web_permission
from openclear.web.form_errors import friendly_error
from openclear.web.redirects import see_other
from openclear.web.security import verify_csrf

router = APIRouter(prefix="/ui/paid-items", tags=["web-paid-items"])


@router.get("")
def list_paid_items(
    request: Request, page: int = 1, db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("paid_item:read")),
):
    from openclear.web import list_search

    search = list_search.parse(request)
    repo = PaidItemRepository(db, ctx.tenant_id, ctx.customer_id)
    result = list_search.page_or_export(
        request=request, db=db, ctx=ctx, search=search, spec=list_search.paid_items_spec(), page=page, list_name="paid_items",
        count=lambda where: repo.count(where=where), fetch=lambda **kw: repo.list(**kw),
    )
    if not hasattr(result, "items"):
        return result
    return render_template(request, "paid_items/list.html", ctx=ctx, page_obj=result, search=search)


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
    return see_other(f"/ui/paid-items/{item.id}", flash=flash)


# NOTE: /bulk must be registered before /{item_id} — FastAPI matches path patterns in
# registration order, and "bulk" would otherwise be captured as item_id and rejected as
# an invalid UUID before this route is ever tried. Same class of pitfall as /bulk vs
# /{item_id} elsewhere in this codebase.


def _layout_choices(db: Session, ctx: TenantContext) -> dict:
    """The bank's posted-check layouts (PAID_ITEM_LAYOUTS_PLAN.md PIL-D5), with its default
    chosen; none for a customer's people."""
    from openclear.bulk_import import layouts
    from openclear.services import import_layout_service

    default = import_layout_service.default_for(db, ctx.tenant_id, ctx.customer_id, layouts.PAID_ITEMS) if ctx.customer_id is None else None
    return {"layouts": import_layout_service.visible(db, ctx.tenant_id, ctx.customer_id, layouts.PAID_ITEMS),
            "default_layout_id": default.id if default else None}


@router.get("/bulk")
def bulk_upload_form(
    request: Request, db: Session = Depends(get_db), ctx: TenantContext = Depends(require_web_permission("paid_item:write"))
) -> HTMLResponse:
    return render_template(request, "paid_items/bulk_upload.html", ctx=ctx, **_layout_choices(db, ctx))


@router.post("/bulk")
async def bulk_upload_paid_items(
    request: Request,
    upload_file: UploadFile,
    create_missing_accounts: bool = Form(False),
    layout_id: str = Form(""),
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("paid_item:write")),
    _csrf: None = Depends(verify_csrf),
) -> HTMLResponse:
    """Checks presented for payment as plain delimited data, no image attached — see
    networks/check/bulk_import.py::ingest_paid_item_tabular_rows — in OpenClear's columns or
    read by one of the bank's posted-check layouts. For an image-bearing presentment (a real
    cash letter), see /ui/check-images/bulk instead."""
    from openclear.bulk_import import layouts
    from openclear.services import import_layout_service

    options: dict = {"create_missing_accounts": create_missing_accounts}
    file_format = "tabular"
    if layout_id:
        try:
            layout = import_layout_service.get(db, ctx.tenant_id, uuid.UUID(layout_id), ctx.customer_id, kind=layouts.PAID_ITEMS)
        except (ValueError, import_layout_service.LayoutNotFound):
            raise WebNotFound() from None
        options |= import_layout_service.upload_options(layout)
        file_format = "layout"
    return start_import(
        request, db, ctx, upload_file, kind=BulkUploadKind.PAID_ITEMS, file_format=file_format,
        form_template="paid_items/bulk_upload.html", default_filename="upload.csv",
        options=options, extra_context=_layout_choices(db, ctx),
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
