# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

from openclear.api.v1.pagination import Page, Since, changed_since, page_params, set_page_headers, updated_since_param
from openclear.auth.deps import require_permission
from openclear.db.session import get_db
from openclear.db.tenancy import TenantContext
from openclear.domain.ach_authorization_rule import AchAuthorizationRule
from openclear.domain.ach_authorization_rule import AchAuthorizationStatus
from openclear.repositories.ach_authorization_repo import AchAuthorizationRepository
from openclear.schemas.ach import AchAuthorizationCreate, AchAuthorizationRead
from openclear.services import ach_authorization_service, audit_log_service

router = APIRouter(prefix="/ach/authorizations", tags=["ach-authorizations"])


@router.post("", response_model=AchAuthorizationRead, status_code=status.HTTP_201_CREATED)
def create_ach_authorization(
    payload: AchAuthorizationCreate,
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_permission("ach_authorization:write")),
) -> AchAuthorizationRead:
    try:
        rule = ach_authorization_service.create_ach_authorization(
            db,
            ctx.tenant_id,
            ach_authorization_service.AchAuthorizationInput(**payload.model_dump()),
            created_by_user_id=ctx.user_id,
            scoped_customer_id=ctx.customer_id,
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from None
    audit_log_service.record_action(
        db,
        ctx.tenant_id,
        actor_user_id=ctx.user_id,
        channel="api",
        action="ach_authorization.create",
        summary=f"Authorized ACH originator {rule.originator_name} ({rule.originator_id})",
        resource_type="ach_authorization",
        resource_id=rule.id,
    )
    db.commit()
    return AchAuthorizationRead.model_validate(rule)


@router.get("", response_model=list[AchAuthorizationRead])
def list_ach_authorizations(
    request: Request,
    response: Response,
    status_filter: AchAuthorizationStatus | None = None,
    page: Page = Depends(page_params),
    updated_since: Since | None = Depends(updated_since_param),
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_permission("ach_authorization:read")),
) -> list[AchAuthorizationRead]:
    repo = AchAuthorizationRepository(db, ctx.tenant_id, ctx.customer_id)
    extra, order = changed_since(AchAuthorizationRule, updated_since, (AchAuthorizationRule.created_at, AchAuthorizationRule.id))
    rules = repo.list(status=status_filter, order_by=order, limit=page.limit, offset=page.offset, **extra)
    total = repo.count(status=status_filter, **extra)
    set_page_headers(request, response, page, total, since=updated_since, rows=rules)
    return [AchAuthorizationRead.model_validate(r) for r in rules]


@router.patch("/{rule_id}/revoke", response_model=AchAuthorizationRead)
def revoke_ach_authorization(
    rule_id: uuid.UUID,
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_permission("ach_authorization:write")),
) -> AchAuthorizationRead:
    rule = ach_authorization_service.revoke_ach_authorization(db, ctx.tenant_id, rule_id, scoped_customer_id=ctx.customer_id)
    if rule is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "ACH authorization not found")
    audit_log_service.record_action(
        db,
        ctx.tenant_id,
        actor_user_id=ctx.user_id,
        channel="api",
        action="ach_authorization.revoke",
        summary=f"Revoked ACH authorization for {rule.originator_name} ({rule.originator_id})",
        resource_type="ach_authorization",
        resource_id=rule.id,
    )
    db.commit()
    return AchAuthorizationRead.model_validate(rule)
