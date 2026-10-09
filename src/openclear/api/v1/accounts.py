# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Accounts, for a core or middleware keeping OpenClear in sync (SDK_PLAN.md P0): a paged
list and an upsert by account number. A customer-scoped caller sees and changes only its
own customer's accounts."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from openclear.api.v1.pagination import Page, page_params, set_page_headers
from openclear.auth.deps import require_permission
from openclear.db.session import get_db
from openclear.db.tenancy import TenantContext
from openclear.domain.account import Account, AchDebitBlockMode
from openclear.repositories.account_repo import AccountRepository
from openclear.services import account_service, audit_log_service, customer_service

router = APIRouter(prefix="/accounts", tags=["accounts"])


class AccountUpsert(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    customer_number: str | None = Field(None, max_length=64, description="Whose account it is; null for a bank (house) account")
    ach_debit_block_mode: AchDebitBlockMode | None = None
    external_account_id: str | None = Field(None, max_length=64, description="The customer's own reference, matched in its files")


class AccountRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    account_number: str
    name: str
    customer_id: uuid.UUID | None
    ach_debit_block_mode: AchDebitBlockMode
    external_account_id: str | None


@router.get("", response_model=list[AccountRead])
def list_accounts(
    request: Request, response: Response, page: Page = Depends(page_params), db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_permission("account:read")),
) -> list[AccountRead]:
    repo = AccountRepository(db, ctx.tenant_id, ctx.customer_id)
    rows = repo.list(order_by=(Account.account_number, Account.id), limit=page.limit, offset=page.offset)
    set_page_headers(request, response, page, repo.count())
    return [AccountRead.model_validate(a) for a in rows]


@router.put("/{account_number}", response_model=AccountRead, responses={201: {"model": AccountRead}})
def upsert_account(
    account_number: str, payload: AccountUpsert, db: Session = Depends(get_db), ctx: TenantContext = Depends(require_permission("account:write")),
):
    """Creates the account with this number, or updates it to match. `200` when it existed,
    `201` when it was created."""
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
    db.commit()
    return JSONResponse(AccountRead.model_validate(account).model_dump(mode="json"), status_code=code)
