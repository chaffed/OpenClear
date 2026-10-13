# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from openclear.auth.deps import require_permission
from openclear.db.session import get_db
from openclear.db.tenancy import TenantContext
from openclear.schemas.user import AccountAccessRead, AccountAccessUpdate, TenantUserRead
from openclear.services import account_access_service, audit_log_service, user_service

router = APIRouter(prefix="/users", tags=["users"])


@router.get("", response_model=list[TenantUserRead])
def list_users(
    db: Session = Depends(get_db), ctx: TenantContext = Depends(require_permission("user:manage"))
) -> list[TenantUserRead]:
    """Read-only access review / recertification endpoint — one row per
    TenantMembership, reusing the exact same query that powers /ui/users so the two can
    never drift apart."""
    rows = user_service.list_tenant_users(db, ctx.tenant_id)
    limited = {row.membership.id: sorted(account_access_service.chosen_accounts(db, row.membership.id), key=str)
               for row in rows if row.membership.customer_id is not None and not row.membership.all_accounts}
    return [
        TenantUserRead(
            user_id=row.user.id,
            email=row.user.email,
            security_group_name=row.security_group_name,
            customer_name=row.customer_name,
            is_active=row.membership.is_active,
            membership_id=row.membership.id,
            membership_created_at=row.membership.created_at,
            last_login_at=row.user.last_login_at,
            all_accounts=row.membership.id not in limited,
            account_ids=limited.get(row.membership.id, []),
        )
        for row in rows
    ]


def _access_read(access) -> AccountAccessRead:
    return AccountAccessRead(membership_id=access.membership.id, customer_id=access.membership.customer_id,
                             all_accounts=access.all_accounts, account_ids=sorted(access.account_ids, key=str))


@router.get("/{membership_id}/accounts", response_model=AccountAccessRead)
def get_account_access(
    membership_id: uuid.UUID, db: Session = Depends(get_db), ctx: TenantContext = Depends(require_permission("user:manage")),
) -> AccountAccessRead:
    """Which of the customer's accounts this customer membership sees."""
    try:
        return _access_read(account_access_service.get_access(db, ctx.tenant_id, membership_id))
    except account_access_service.AccountAccessError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None


@router.put("/{membership_id}/accounts", response_model=AccountAccessRead)
def set_account_access(
    membership_id: uuid.UUID, body: AccountAccessUpdate, db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_permission("user:manage")),
) -> AccountAccessRead:
    """Limits a customer membership to some of the customer's accounts, or lets it see all
    of them again. Takes effect on that person's next request."""
    try:
        access = account_access_service.get_access(db, ctx.tenant_id, membership_id)
    except account_access_service.AccountAccessError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    try:
        access = account_access_service.set_access(db, ctx.tenant_id, membership_id, all_accounts=body.all_accounts,
                                                   account_ids=body.account_ids)
    except account_access_service.AccountAccessError as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from None
    audit_log_service.record_action(
        db, ctx.tenant_id, actor_user_id=ctx.user_id, channel="api", action="user.account_access",
        summary=f"Account access for membership {membership_id}: {account_access_service.describe(access)}",
        resource_type="user", resource_id=access.membership.user_id,
    )
    db.commit()
    return _access_read(access)
