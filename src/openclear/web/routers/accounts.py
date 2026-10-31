# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import uuid

from fastapi import APIRouter, Depends, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from openclear.db.session import get_db
from openclear.db.tenancy import TenantContext
from openclear.domain.account import Account, CheckReviewMode
from openclear.domain.bulk_upload_file import BulkUploadKind
from openclear.repositories.account_repo import AccountRepository
from openclear.services import account_service, audit_log_service, customer_service
from openclear.services import custom_field_service as custom_fields
from openclear.services.custom_field_service import CustomFieldError
from openclear.web.bulk_import import start_import
from openclear.web.deps import WebNotFound, render_template, require_web_permission
from openclear.web.pagination import paginate
from openclear.web.redirects import checklist_return, see_other
from openclear.web.security import verify_csrf

router = APIRouter(prefix="/ui/accounts", tags=["web-accounts"])


@router.get("")
def list_accounts(
    request: Request, page: int = 1, field: str = "", value: str = "", missing: str = "", db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("account:read")),
) -> HTMLResponse:
    """With `field` and `value`, the accounts whose custom field has exactly that value; with
    `missing=1`, those lacking a required one (plans/CUSTOM_FIELDS_PLAN.md CF-D5, CF-D6)."""
    from openclear.domain.custom_field import CustomField, CustomFieldValue

    fields = custom_fields.list_fields(db, ctx.tenant_id, "account")
    if ctx.customer_id is not None:
        fields = [f for f in fields if f.shown_to_customer]  # a customer's people see what's shown to them
    where = []
    chosen = next((f for f in fields if f.key == field), None) if field else None
    if chosen and value.strip():
        where.append(Account.id.in_(select(CustomFieldValue.account_id).where(
            CustomFieldValue.field_id == chosen.id, CustomFieldValue.text_value == value.strip())))
    if missing == "1" and ctx.customer_id is None:
        condition = custom_fields.missing_required_condition(db, ctx.tenant_id, "account")
        if condition is not None:
            where.append(condition)
    repo = AccountRepository(db, ctx.tenant_id, ctx.customer_id)
    page_obj = paginate(
        page=page,
        count_fn=lambda: repo.count(where=where),
        list_fn=lambda **kw: repo.list(where=where, order_by=Account.account_number, **kw),
    )
    # A customer-scoped user's own scope is implicit — no picker needed, and they must
    # never be offered other customers (or "unassigned") to file a new account under.
    customers = customer_service.list_customers(db, ctx.tenant_id) if ctx.customer_id is None else []
    customer_names = {c.id: c.name for c in customers}
    return render_template(request, "accounts/list.html", ctx=ctx, page_obj=page_obj, customer_names=customer_names,
                           custom_fields=fields, list_fields=[f for f in fields if f.show_in_list],
                           custom_columns=[{"key": f"cf:{f.key}", "label": f.label} for f in fields if f.show_in_list],
                           custom_values=custom_fields.values_for(db, ctx.tenant_id, "account", [a.id for a in page_obj.items],
                                                                  customer_id=ctx.customer_id),
                           filter_field=field, filter_value=value, missing=missing == "1",
                           any_required=ctx.customer_id is None and any(f.required for f in fields))


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
    # A customer's people create accounts too, but never write custom fields (CF-D3).
    fields = custom_fields.list_fields(db, ctx.tenant_id, "account") if ctx.customer_id is None else []
    return render_template(
        request, "accounts/form.html", ctx=ctx, customers=customers, error=error, form=form or {},
        custom_fields=fields, custom_values={}, status_code=400 if error else 200,
    )


async def _custom_values(request: Request, db: Session, ctx: TenantContext) -> dict[str, str]:
    form = await request.form()
    return {f.key: str(form.get(f"cf_{f.key}") or "") for f in custom_fields.list_fields(db, ctx.tenant_id, "account")}


@router.post("")
async def create_account(
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
            "return": checklist_return(return_to) or "", **{k: v for k, v in (await request.form()).items() if k.startswith("cf_")}}
    values = await _custom_values(request, db, ctx) if ctx.customer_id is None else {}
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
    try:
        custom_fields.set_values(db, ctx.tenant_id, "account", account.id, values, user_id=ctx.user_id, require=ctx.customer_id is None)
    except CustomFieldError as exc:
        db.rollback()
        return _render_account_form(request, db, ctx, form=form, error=str(exc))
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


@router.get("/{account_id}/fields")
def account_fields_form(request: Request, account_id: uuid.UUID, db: Session = Depends(get_db),
                        ctx: TenantContext = Depends(require_web_permission("account:write"))) -> HTMLResponse:
    """An account's custom fields, for the bank's staff (plans/CUSTOM_FIELDS_PLAN.md CF2)."""
    account = _bank_account(db, ctx, account_id)
    return _render_fields_form(request, db, ctx, account)


def _render_fields_form(request: Request, db: Session, ctx: TenantContext, account: Account, *, error: str | None = None,
                        form: dict | None = None) -> HTMLResponse:
    customer = customer_service.get_customer(db, ctx.tenant_id, account.customer_id) if account.customer_id else None
    all_values = custom_fields.values_of(db, ctx.tenant_id, "account", account.id)  # one read for the live and the archived fields
    return render_template(request, "accounts/fields.html", ctx=ctx, account=account, customer=customer, error=error, form=form,
                           custom_fields=custom_fields.list_fields(db, ctx.tenant_id, "account"),
                           custom_values=custom_fields.values_of(db, ctx.tenant_id, "account", account.id, include_archived=False),
                           archived_values={f.key: all_values[f.key] for f in custom_fields.list_fields(db, ctx.tenant_id, "account", include_archived=True)
                                            if f.archived_at and all_values.get(f.key) is not None},
                           status_code=422 if error else 200)


@router.post("/{account_id}/fields")
async def save_account_fields(request: Request, account_id: uuid.UUID, db: Session = Depends(get_db),
                              ctx: TenantContext = Depends(require_web_permission("account:write")),
                              _csrf: None = Depends(verify_csrf)) -> HTMLResponse:
    account = _bank_account(db, ctx, account_id)
    values = await _custom_values(request, db, ctx)
    try:
        custom_fields.set_values(db, ctx.tenant_id, "account", account.id, values, user_id=ctx.user_id, require=True)
    except CustomFieldError as exc:
        db.rollback()
        return _render_fields_form(request, db, ctx, account, error=str(exc), form=dict(await request.form()))
    db.commit()
    return see_other(f"/ui/accounts/{account.id}/fields", flash="Saved.")


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
