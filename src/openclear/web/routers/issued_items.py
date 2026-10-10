# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import uuid
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from openclear.db.session import get_db
from openclear.db.tenancy import TenantContext
from openclear.domain.bulk_upload_file import BulkUploadKind
from openclear.domain.issued_item import IssuedItemStatus
from openclear.repositories.issued_item_repo import IssuedItemRepository
from openclear.services import account_service, audit_log_service, issued_item_service
from openclear.web.bulk_import import start_import
from openclear.web.deps import WebNotFound, render_template, require_web_permission
from openclear.web.form_errors import friendly_error
from openclear.web.security import verify_csrf

router = APIRouter(prefix="/ui/issued-items", tags=["web-issued-items"])


@router.get("")
def list_issued_items(
    request: Request,
    status: IssuedItemStatus | None = None,
    page: int = 1,
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("issued_item:read")),
):
    from openclear.web import list_search

    search = list_search.parse(request, extra=("status",))
    repo = IssuedItemRepository(db, ctx.tenant_id, ctx.customer_id)
    result = list_search.page_or_export(
        request=request, db=db, ctx=ctx, search=search, spec=list_search.issued_items_spec(), page=page, list_name="issued_items",
        count=lambda where: repo.count(status=status, where=where),
        fetch=lambda **kw: repo.list(status=status, **kw),
    )
    if not hasattr(result, "items"):
        return result
    return render_template(request, "issued_items/list.html", ctx=ctx, page_obj=result, status_filter=status, search=search)


@router.get("/new")
def new_issued_item_form(
    request: Request,
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("issued_item:write")),
) -> HTMLResponse:
    accounts = account_service.list_accounts(db, ctx.tenant_id, customer_id=ctx.customer_id)
    return render_template(request, "issued_items/form.html", ctx=ctx, accounts=accounts)


@router.post("")
def create_issued_item(
    request: Request,
    account_id: uuid.UUID = Form(...),
    check_number: str = Form(...),
    amount: str = Form(...),
    payee_name: str = Form(...),
    issue_date: str = Form(...),
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("issued_item:write")),
    _csrf: None = Depends(verify_csrf),
) -> HTMLResponse:
    from datetime import date
    from decimal import Decimal, InvalidOperation

    try:
        parsed_amount = Decimal(amount)
        parsed_issue_date = date.fromisoformat(issue_date)
    except (InvalidOperation, ValueError):
        accounts = account_service.list_accounts(db, ctx.tenant_id, customer_id=ctx.customer_id)
        return render_template(
            request,
            "issued_items/form.html",
            ctx=ctx,
            accounts=accounts,
            error="Invalid amount or date.",
            status_code=422,
        )

    try:
        item = issued_item_service.create_issued_item(
            db,
            ctx.tenant_id,
            issued_item_service.IssuedItemInput(
                account_id=account_id,
                check_number=check_number,
                amount=parsed_amount,
                payee_name=payee_name,
                issue_date=parsed_issue_date,
            ),
            submitted_by_user_id=ctx.user_id,
            scoped_customer_id=ctx.customer_id,
        )
        audit_log_service.record_action(
            db,
            ctx.tenant_id,
            actor_user_id=ctx.user_id,
            channel="web",
            action="issued_item.create",
            summary=f"Issued check #{item.check_number} for {item.amount} to {item.payee_name}",
            resource_type="issued_item",
            resource_id=item.id,
        )
        db.commit()
    except Exception as exc:  # noqa: BLE001 — surface a DB constraint violation (e.g. duplicate check number) to the form
        db.rollback()
        accounts = account_service.list_accounts(db, ctx.tenant_id, customer_id=ctx.customer_id)
        return render_template(
            request,
            "issued_items/form.html",
            ctx=ctx,
            accounts=accounts,
            error=friendly_error(exc, action="Could not create issued item", duplicate="An issued check with that number already exists on this account."),
            status_code=422,
        )

    return RedirectResponse("/ui/issued-items?flash=Issued+item+created.", status_code=303)


# NOTE: /bulk must be registered before /{item_id} below — FastAPI matches path patterns
# in registration order, and "bulk" would otherwise be captured as item_id and rejected
# as an invalid UUID before this route is ever tried.


@router.get("/bulk")
def bulk_upload_form(
    request: Request, ctx: TenantContext = Depends(require_web_permission("issued_item:write"))
) -> HTMLResponse:
    return render_template(request, "issued_items/bulk_upload.html", ctx=ctx)


@router.post("/bulk")
async def bulk_upload_issued_items(
    request: Request,
    upload_file: UploadFile,
    create_missing_accounts: bool = Form(False),
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("issued_item:write")),
    _csrf: None = Depends(verify_csrf),
) -> HTMLResponse:
    return start_import(
        request, db, ctx, upload_file, kind=BulkUploadKind.ISSUED_ITEMS, file_format="tabular",
        form_template="issued_items/bulk_upload.html", default_filename="upload.csv",
        options={"create_missing_accounts": create_missing_accounts},
    )


@router.get("/{item_id}")
def issued_item_detail(
    request: Request,
    item_id: uuid.UUID,
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("issued_item:read")),
) -> HTMLResponse:
    item = IssuedItemRepository(db, ctx.tenant_id, ctx.customer_id).get(item_id)
    if item is None:
        raise WebNotFound()
    return render_template(request, "issued_items/detail.html", ctx=ctx, item=item)


@router.post("/{item_id}/void")
def void_issued_item(
    item_id: uuid.UUID,
    reason: str = Form(...),
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("issued_item:write")),
    _csrf: None = Depends(verify_csrf),
) -> RedirectResponse:
    item = issued_item_service.void_issued_item(db, ctx.tenant_id, item_id, reason, scoped_customer_id=ctx.customer_id)
    if item is None:
        db.commit()
        return RedirectResponse(f"/ui/issued-items?error={quote('Issued item not found.')}", status_code=303)
    audit_log_service.record_action(
        db,
        ctx.tenant_id,
        actor_user_id=ctx.user_id,
        channel="web",
        action="issued_item.void",
        summary=f"Voided check #{item.check_number}: {reason}",
        resource_type="issued_item",
        resource_id=item.id,
    )
    db.commit()
    return RedirectResponse(f"/ui/issued-items/{item_id}?flash=Issued+item+voided.", status_code=303)
