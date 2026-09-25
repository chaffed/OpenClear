# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import uuid

from fastapi import APIRouter, Depends, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from openclear.bulk_import.tabular import TabularParseError, parse_tabular_file
from openclear.db.session import get_db
from openclear.db.tenancy import TenantContext
from openclear.domain.account import Account
from openclear.domain.bulk_upload_file import BulkUploadKind
from openclear.repositories.account_repo import AccountRepository
from openclear.services import (
    account_service,
    audit_log_service,
    bulk_upload_file_service,
    bulk_upload_reversal_service,
    customer_service,
)
from openclear.web.deps import render_template, require_web_permission
from openclear.web.pagination import paginate
from openclear.web.security import verify_csrf

router = APIRouter(prefix="/ui/accounts", tags=["web-accounts"])


@router.get("")
def list_accounts(
    request: Request, page: int = 1, db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("account:read")),
) -> HTMLResponse:
    page_obj = paginate(
        page=page,
        count_fn=lambda: AccountRepository(db, ctx.tenant_id, ctx.customer_id).count(),
        list_fn=lambda **kw: account_service.list_accounts(
            db, ctx.tenant_id, customer_id=ctx.customer_id, order_by=Account.account_number, **kw
        ),
    )
    # A customer-scoped user's own scope is implicit — no picker needed, and they must
    # never be offered other customers (or "unassigned") to file a new account under.
    customers = customer_service.list_customers(db, ctx.tenant_id) if ctx.customer_id is None else []
    customer_names = {c.id: c.name for c in customers}
    return render_template(request, "accounts/list.html", ctx=ctx, page_obj=page_obj, customer_names=customer_names)


@router.get("/new")
def new_account_form(
    request: Request, db: Session = Depends(get_db), ctx: TenantContext = Depends(require_web_permission("account:write"))
) -> HTMLResponse:
    return _render_account_form(request, db, ctx)


def _render_account_form(
    request: Request, db: Session, ctx: TenantContext, *, error: str | None = None, form: dict | None = None
) -> HTMLResponse:
    customers = customer_service.list_customers(db, ctx.tenant_id) if ctx.customer_id is None else []
    return render_template(
        request, "accounts/form.html", ctx=ctx, customers=customers, error=error, form=form or {},
        status_code=400 if error else 200,
    )


@router.post("")
def create_account(
    request: Request,
    account_number: str = Form(...),
    name: str = Form(...),
    customer_id: str = Form(""),
    external_account_id: str = Form(""),
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("account:write")),
    _csrf: None = Depends(verify_csrf),
) -> RedirectResponse:
    form = {"account_number": account_number, "name": name, "external_account_id": external_account_id, "customer_id": customer_id}
    if ctx.customer_id is not None:
        resolved_customer_id = ctx.customer_id
    elif customer_id:
        # Never trust a raw id from the form: it must parse, and must be one of THIS
        # tenant's customers (repository-scoped lookup), not merely some customer row.
        try:
            parsed_customer_id = uuid.UUID(customer_id)
        except ValueError:
            parsed_customer_id = None
        if parsed_customer_id is None or customer_service.get_customer(db, ctx.tenant_id, parsed_customer_id) is None:
            return _render_account_form(request, db, ctx, error="Please choose a customer from the list.", form=form)
        resolved_customer_id = parsed_customer_id
    else:
        resolved_customer_id = None
    try:
        account = account_service.create_account(
            db, ctx.tenant_id, account_service.AccountInput(
                account_number=account_number, name=name, customer_id=resolved_customer_id,
                external_account_id=external_account_id or None,
            )
        )
    except IntegrityError:
        db.rollback()
        return _render_account_form(
            request, db, ctx, form=form,
            error="An account with that account number or external account ID already exists.",
        )
    audit_log_service.record_action(
        db,
        ctx.tenant_id,
        actor_user_id=ctx.user_id,
        channel="web",
        action="account.create",
        summary=f"Created account {account.account_number} ({account.name})",
        resource_type="account",
        resource_id=account.id,
    )
    db.commit()
    return RedirectResponse("/ui/accounts?flash=Account+created.", status_code=303)


@router.get("/bulk")
def bulk_upload_form(
    request: Request, ctx: TenantContext = Depends(require_web_permission("account:write"))
) -> HTMLResponse:
    return render_template(request, "accounts/bulk_upload.html", ctx=ctx)


@router.post("/bulk")
async def bulk_upload_accounts(
    request: Request,
    upload_file: UploadFile,
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("account:write")),
    _csrf: None = Depends(verify_csrf),
) -> HTMLResponse:
    content = await upload_file.read()

    # Recorded before parsing (and committed on its own) so a rejected/malformed file is
    # still captured for audit purposes, not just successful uploads — see
    # services/bulk_upload_file_service.py.
    upload_record = bulk_upload_file_service.record_uploaded_file(
        db,
        ctx.tenant_id,
        kind=BulkUploadKind.ACCOUNTS,
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
            request, "accounts/bulk_upload.html", ctx=ctx, error=str(exc), status_code=422, upload_record=upload_record
        )
    if not rows:
        return render_template(
            request,
            "accounts/bulk_upload.html",
            ctx=ctx,
            error="That file has no data rows.",
            status_code=422,
            upload_record=upload_record,
        )

    results = account_service.create_accounts_from_rows(db, ctx.tenant_id, rows, scoped_customer_id=ctx.customer_id)
    for r in results:
        if not r.success:
            continue
        audit_log_service.record_action(
            db,
            ctx.tenant_id,
            actor_user_id=ctx.user_id,
            channel="web",
            action="account.create",
            summary=f"Account created via bulk upload ({r.row_label})",
            resource_type="account",
            resource_id=r.created_id,
        )
        bulk_upload_reversal_service.track_created_record(
            db, ctx.tenant_id, upload_record.id, resource_type="account", resource_id=r.created_id, row_label=r.row_label
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
        back_url="/ui/accounts",
        back_label="Back to accounts",
    )
