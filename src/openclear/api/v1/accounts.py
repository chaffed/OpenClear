# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Accounts, for a core or middleware keeping OpenClear in sync (SDK_PLAN.md P0): a paged
list and an upsert by account number. A customer-scoped caller sees and changes only its
own customer's accounts."""

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from openclear.api.v1.pagination import Page, Since, changed_since, page_params, set_page_headers, updated_since_param
from openclear.auth.deps import require_permission
from openclear.db.session import get_db
from openclear.db.tenancy import TenantContext
from openclear.domain.account import Account, AchDebitBlockMode, CheckReviewMode
from openclear.repositories.account_repo import AccountRepository
from openclear.services import account_service, audit_log_service, customer_service
from openclear.services import custom_field_service as custom_fields

router = APIRouter(prefix="/accounts", tags=["accounts"])


class AccountUpsert(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    customer_number: str | None = Field(None, max_length=64, description="Whose account it is; null for a bank (house) account")
    ach_debit_block_mode: AchDebitBlockMode | None = None
    external_account_id: str | None = Field(None, max_length=64, description="The customer's own reference, matched in its files")
    check_review_mode: CheckReviewMode | None = Field(
        None, description="positive, or reverse positive pay: the customer reviews every check and unreturned ones are paid at "
                          "the cut-off. Bank staff with admin:manage only; turning reverse on may wait for a second admin (202).")
    custom_fields: dict[str, str | None] | None = Field(
        None, description="The bank's custom fields by key, as strings (GET /custom-fields gives each one's type); null clears one. "
                          "Keys left out keep their values; left out entirely, nothing changes. Bank-level callers only.")


class AccountRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    account_number: str
    name: str
    customer_id: uuid.UUID | None
    ach_debit_block_mode: AchDebitBlockMode
    check_review_mode: CheckReviewMode
    external_account_id: str | None
    updated_at: datetime | None = None
    custom_fields: dict[str, str | None] = Field({}, description="The bank's custom fields by key, as strings; a customer-scoped "
                                                                  "caller gets only those shown to the customer")


def _with_fields(db: Session, ctx: TenantContext, rows) -> list[AccountRead]:
    values = custom_fields.values_for(db, ctx.tenant_id, "account", [a.id for a in rows], customer_id=ctx.customer_id)
    return [AccountRead.model_validate(a).model_copy(update={"custom_fields": values.get(a.id, {})}) for a in rows]


@router.get("", response_model=list[AccountRead])
def list_accounts(
    request: Request, response: Response, page: Page = Depends(page_params), db: Session = Depends(get_db),
    updated_since: Since | None = Depends(updated_since_param),
    ctx: TenantContext = Depends(require_permission("account:read")),
) -> list[AccountRead]:
    repo = AccountRepository(db, ctx.tenant_id, ctx.customer_id)
    extra, order = changed_since(Account, updated_since, (Account.account_number, Account.id))
    rows = repo.list(order_by=order, limit=page.limit, offset=page.offset, **extra)
    set_page_headers(request, response, page, repo.count(**extra), since=updated_since, rows=rows)
    return _with_fields(db, ctx, rows)


@router.put("/{account_number}", response_model=AccountRead, responses={201: {"model": AccountRead}, 202: {
    "description": "Saved, except reverse positive pay, which waits for a second admin (Settings → Changes)",
    "content": {"application/json": {"example": {"status": "pending", "change_request_id": "…", "detail": "…"}}}}})
def upsert_account(
    account_number: str, payload: AccountUpsert, db: Session = Depends(get_db), ctx: TenantContext = Depends(require_permission("account:write")),
):
    """Creates the account with this number, or updates it to match. `200` when it existed,
    `201` when it was created. Putting an account on reverse positive pay (`check_review_mode`)
    needs admin:manage and a bank-level caller; when the bank's sensitive changes need a second
    admin, the rest is saved and this answers `202` with `{"status": "pending", "change_request_id"}`."""
    if payload.check_review_mode is not None and (ctx.customer_id is not None or "admin:manage" not in ctx.permissions):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the bank's administrators set an account's check review")
    if payload.custom_fields and ctx.customer_id is not None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the bank sets an account's custom fields")
    customer_id = None
    if payload.customer_number:
        customer = customer_service.get_customer_by_number(db, ctx.tenant_id, payload.customer_number)
        if customer is None:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"No customer with number {payload.customer_number!r}")
        customer_id = customer.id
    if ctx.customer_id is not None and customer_id != ctx.customer_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "A customer-scoped caller can only sync its own customer's accounts")

    # Exactly this account number: an account visible only under another customer counts
    # as not found for a scoped caller, and then can't be created over it either.
    existing = AccountRepository(db, ctx.tenant_id).list(account_number=account_number)
    if existing and ctx.customer_id is not None and existing[0].customer_id != ctx.customer_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Account not found")
    if existing:
        account = existing[0]
        account.name, account.customer_id, account.external_account_id = payload.name, customer_id, payload.external_account_id
        if payload.ach_debit_block_mode is not None:
            account.ach_debit_block_mode = payload.ach_debit_block_mode
        db.flush()
        action, code = "account.update", status.HTTP_200_OK
    else:
        account = account_service.create_account(db, ctx.tenant_id, account_service.AccountInput(
            account_number=account_number, name=payload.name, customer_id=customer_id,
            ach_debit_block_mode=payload.ach_debit_block_mode, external_account_id=payload.external_account_id,
        ))
        action, code = "account.create", status.HTTP_201_CREATED
    audit_log_service.record_action(
        db, ctx.tenant_id, actor_user_id=ctx.user_id, channel="api", action=action,
        summary=f"{'Created' if code == 201 else 'Synced'} account {account_number} ({payload.name})",
        resource_type="account", resource_id=account.id,
    )
    if payload.custom_fields:
        try:
            custom_fields.set_values(db, ctx.tenant_id, "account", account.id, payload.custom_fields, user_id=ctx.user_id, channel="api")
        except custom_fields.CustomFieldError as exc:
            db.rollback()
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from None
    if payload.check_review_mode is not None:
        from openclear.services import change_request_service

        try:
            outcome = change_request_service.request_or_apply(
                db, "account.check_review_mode", {"account_id": str(account.id), "mode": payload.check_review_mode.value},
                tenant_id=ctx.tenant_id, user_id=ctx.user_id, channel="api")
        except ValueError as exc:
            db.rollback()
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None
        if outcome.waiting:
            db.commit()
            return JSONResponse({"status": "pending", "change_request_id": str(outcome.change.id),
                                 "detail": "The rest is saved. Reverse positive pay waits for a second admin (Settings → Changes, "
                                           "or POST /api/v1/changes/{id}/approve)."}, status_code=status.HTTP_202_ACCEPTED)
        if outcome.summary != "Nothing changed.":
            audit_log_service.record_action(db, ctx.tenant_id, actor_user_id=ctx.user_id, channel="api", action="account.check_review_mode",
                                            summary=outcome.summary, resource_type="account", resource_id=account.id)
    db.commit()
    return JSONResponse(_with_fields(db, ctx, [account])[0].model_dump(mode="json"), status_code=code)
