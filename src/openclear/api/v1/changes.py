# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Settings changes waiting for a second person (DUAL_CONTROL_PLAN.md;
services/change_request_service.py). A call that makes a protected change answers `202` with
`change_request_id`; another person who could have made it approves it here, or in Settings →
Changes. Whoever asked can withdraw it; a rejection needs a reason. A customer's own session
sees its own customer's."""

import uuid
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from openclear.auth.deps import get_current_context
from openclear.db.session import get_db
from openclear.db.tenancy import TenantContext
from openclear.services import change_request_service
from openclear.services.change_kinds import get as kind_of

router = APIRouter(prefix="/changes", tags=["changes"])


class ChangeRead(BaseModel):
    id: uuid.UUID
    scope: Literal["provider", "bank", "customer"]
    customer_id: uuid.UUID | None
    kind: str
    title: str  # what kind of setting
    summary: str
    before: dict[str, Any] | None
    after: dict[str, Any] | None
    reason: str | None
    status: Literal["pending", "approved", "rejected", "withdrawn", "expired", "applied_alone"]
    requested_by_user_id: uuid.UUID | None
    requested_at: datetime
    expires_at: datetime | None
    applies_at: datetime | None  # set when nobody else can approve: it applies then unless withdrawn or rejected
    decided_by_user_id: uuid.UUID | None
    decided_at: datetime | None
    decided_reason: str | None
    can_decide: bool  # whether the caller could approve or reject it


class Rejection(BaseModel):
    reason: str = Field(..., min_length=1, max_length=2000)


def _read(db: Session, ctx: TenantContext, change) -> dict:
    return {**{c: getattr(change, c) for c in ChangeRead.model_fields if hasattr(change, c)}, "title": kind_of(change.kind).title,
            "can_decide": change.status == "pending" and change_request_service.can_decide(db, change, ctx.user_id)}


def _mine(db: Session, ctx: TenantContext, change_id: uuid.UUID):
    change = change_request_service.get(db, ctx.tenant_id, change_id)
    if change is None or (ctx.customer_id is not None and change.customer_id != ctx.customer_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such change.")
    return change


@router.get("", response_model=list[ChangeRead])
def list_changes(db: Session = Depends(get_db), ctx: TenantContext = Depends(get_current_context)) -> list:
    """The changes waiting for a second person in your scope, oldest first."""
    rows = change_request_service.pending(db, ctx.tenant_id, customer_id=ctx.customer_id, all_customers=ctx.customer_id is None)
    return [_read(db, ctx, c) for c in rows]


@router.get("/{change_id}", response_model=ChangeRead)
def get_change(change_id: uuid.UUID, db: Session = Depends(get_db), ctx: TenantContext = Depends(get_current_context)) -> dict:
    return _read(db, ctx, _mine(db, ctx, change_id))


def _decide(db: Session, ctx: TenantContext, change_id: uuid.UUID, verdict: str, reason: str | None = None) -> dict:
    from openclear.auth.sod import SodViolation

    change = _mine(db, ctx, change_id)
    try:
        if verdict == "withdraw":
            change_request_service.withdraw(db, ctx.tenant_id, change.id, user_id=ctx.user_id, channel="api")
        else:
            change_request_service.decide(db, ctx.tenant_id, change.id, approve=verdict == "approve", user_id=ctx.user_id, reason=reason,
                                          channel="api")
    except SodViolation as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, {"code": "ERR_SOD_VIOLATION", "message": str(exc)}) from None
    except change_request_service.ChangeRequestError as exc:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None
    db.commit()
    return _read(db, ctx, change)


@router.post("/{change_id}/approve", response_model=ChangeRead)
def approve_change(change_id: uuid.UUID, db: Session = Depends(get_db), ctx: TenantContext = Depends(get_current_context)) -> dict:
    """Approve a change you could have made yourself; it takes effect at once. Refused (`409`) if
    the setting changed since it was asked for, and (`403`) for whoever asked."""
    return _decide(db, ctx, change_id, "approve")


@router.post("/{change_id}/reject", response_model=ChangeRead)
def reject_change(change_id: uuid.UUID, body: Rejection, db: Session = Depends(get_db),
                  ctx: TenantContext = Depends(get_current_context)) -> dict:
    return _decide(db, ctx, change_id, "reject", body.reason)


@router.post("/{change_id}/withdraw", response_model=ChangeRead)
def withdraw_change(change_id: uuid.UUID, db: Session = Depends(get_db), ctx: TenantContext = Depends(get_current_context)) -> dict:
    """Withdraw a change you asked for."""
    return _decide(db, ctx, change_id, "withdraw")
