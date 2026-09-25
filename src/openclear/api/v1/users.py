# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from openclear.auth.deps import require_permission
from openclear.db.session import get_db
from openclear.db.tenancy import TenantContext
from openclear.schemas.user import TenantUserRead
from openclear.services import user_service

router = APIRouter(prefix="/users", tags=["users"])


@router.get("", response_model=list[TenantUserRead])
def list_users(
    db: Session = Depends(get_db), ctx: TenantContext = Depends(require_permission("user:manage"))
) -> list[TenantUserRead]:
    """Read-only access review / recertification endpoint — one row per
    TenantMembership, reusing the exact same query that powers /ui/users so the two can
    never drift apart."""
    rows = user_service.list_tenant_users(db, ctx.tenant_id)
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
        )
        for row in rows
    ]
