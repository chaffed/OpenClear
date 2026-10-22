# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import uuid

from fastapi import APIRouter, Depends, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from openclear.db.session import get_db
from openclear.db.tenancy import TenantContext
from openclear.domain.account import Account, CheckReviewMode
from openclear.domain.bulk_upload_file import BulkUploadKind
from openclear.repositories.account_repo import AccountRepository
from openclear.services import account_service, audit_log_service, customer_service
from openclear.web.bulk_import import start_import
from openclear.web.deps import WebNotFound, render_template, require_web_permission
from openclear.web.pagination import paginate
from openclear.web.redirects import checklist_return, see_other
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
    request: Request, customer_id: str = "", db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("account:write")),
) -> HTMLResponse:
    # From a customer's checklist: that customer chosen, and saving goes back to the checklist.
    return _render_account_form(request, db, ctx, form={"customer_id": customer_id,
                                                        "return": checklist_return(request.query_params.get("return")) or ""})


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
    return_to: str = Form("", alias="return"),
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("account:write")),
    _csrf: None = Depends(verify_csrf),
) -> RedirectResponse:
    form = {"account_number": account_number, "name": name, "external_account_id": external_account_id, "customer_id": customer_id,
            "return": checklist_return(return_to) or ""}
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
    return see_other(checklist_return(return_to) or "/ui/accounts", flash="Account created.")


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
    return start_import(
        request, db, ctx, upload_file, kind=BulkUploadKind.ACCOUNTS, file_format="tabular",
        form_template="accounts/bulk_upload.html", default_filename="upload.csv",
    )


# --- Reverse positive pay (REVERSE_POSITIVE_PAY_PLAN.md RPP-D1, RPP-D4) ----------------------------


def _bank_account(db: Session, ctx: TenantContext, account_id: uuid.UUID) -> Account:
    """One of this bank's accounts, for its staff: a customer's people never change how their
    checks are reviewed."""
    account = db.get(Account, account_id)
    if account is None or account.tenant_id != ctx.tenant_id or ctx.customer_id is not None:
        raise WebNotFound()
    return account


@router.get("/{account_id}/check-review")
def check_review_form(
    request: Request, account_id: uuid.UUID, db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("admin:manage")),
) -> HTMLResponse:
    account = _bank_account(db, ctx, account_id)
    customer = customer_service.get_customer(db, ctx.tenant_id, account.customer_id) if account.customer_id else None
    return render_template(request, "accounts/check_review.html", ctx=ctx, account=account, customer=customer)


@router.post("/{account_id}/check-review")
def set_check_review(
    account_id: uuid.UUID,
    mode: CheckReviewMode = Form(...),
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("admin:manage")),
    _csrf: None = Depends(verify_csrf),
) -> RedirectResponse:
    """Reverse pays the checks nobody returns, so turning it on waits for a second admin
    under the bank's rule; back to positive pay takes effect at once."""
    from openclear.services import change_request_service
    from openclear.web.routers.tenant_settings import waiting_message

    account = _bank_account(db, ctx, account_id)
    back = f"/ui/accounts/{account.id}/check-review"
    try:
        outcome = change_request_service.request_or_apply(
            db, "account.check_review_mode", {"account_id": str(account.id), "mode": mode.value},
            tenant_id=ctx.tenant_id, user_id=ctx.user_id,
            summary=f"Account {account.account_number}: {'reverse' if mode == CheckReviewMode.REVERSE else 'positive'} positive pay")
    except ValueError as exc:
        db.rollback()
        return see_other(back, error=str(exc))
    if outcome.waiting:
        db.commit()
        return see_other(back, flash=waiting_message(outcome))
    if outcome.summary != "Nothing changed.":
        audit_log_service.record_action(db, ctx.tenant_id, actor_user_id=ctx.user_id, channel="web", action="account.check_review_mode",
                                        summary=outcome.summary, resource_type="account", resource_id=account.id)
    db.commit()
    return see_other(back, flash="Saved.")
