# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Embedding (SDK_PLAN.md P2; services/embed_service.py): a launch link for the person an
online banking server is acting for."""

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from openclear.auth.deps import get_current_context
from openclear.config import get_settings
from openclear.db.session import get_db
from openclear.db.tenancy import TenantContext
from openclear.domain.tenant_membership import TenantMembership
from openclear.services import audit_log_service, embed_service

router = APIRouter(prefix="/embed", tags=["embed"])


class EmbedLaunchCreate(BaseModel):
    parent_origin: str = Field(..., description="The online banking origin that will frame the page, e.g. https://banking.example.com")
    path: str = Field("/ui/exceptions", description="The page to open, e.g. /ui/exceptions")


class EmbedLaunchRead(BaseModel):
    url: str
    expires_in: int


@router.post("/launch", response_model=EmbedLaunchRead, status_code=status.HTTP_201_CREATED)
def create_launch(
    payload: EmbedLaunchCreate, request: Request, db: Session = Depends(get_db), ctx: TenantContext = Depends(get_current_context),
) -> EmbedLaunchRead:
    """A one-time link (good for a minute) that opens `path` in a frame on `parent_origin`
    as the person this call acts for. Only for an API client acting for someone: an
    exchanged token, OpenClear-Act-As or OpenClear-Act-As-Subject."""
    if ctx.acting_api_client_id is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only an API client acting for a person can start an embedded session")
    membership = db.execute(select(TenantMembership).where(
        TenantMembership.user_id == ctx.user_id, TenantMembership.tenant_id == ctx.tenant_id,
        TenantMembership.customer_id == ctx.customer_id, TenantMembership.is_active.is_(True),
    )).scalar_one_or_none()
    if membership is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "No active membership")
    try:
        code = embed_service.create_launch(
            db, ctx.tenant_id, user_id=ctx.user_id, membership_id=membership.id, api_client_id=ctx.acting_api_client_id,
            path=payload.path, parent_origin=payload.parent_origin,
        )
    except embed_service.EmbedError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from None
    audit_log_service.record_action(
        db, ctx.tenant_id, actor_user_id=ctx.user_id, channel="api", action="embed.launch",
        summary=f"Embedded session started from {embed_service.normalize_origin(payload.parent_origin)}", resource_type="user",
        resource_id=ctx.user_id,
    )
    db.commit()
    base = (get_settings().public_base_url or str(request.base_url)).rstrip("/")
    return EmbedLaunchRead(url=f"{base}/ui/embed/launch?code={code}", expires_in=embed_service.LAUNCH_SECONDS)
