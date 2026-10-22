# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""The bank's return reasons (FULL_REVIEW_PLAN.md FR-D12): the ids an API client sends as
`ach_return_reason_id` (required to return an ACH debit) or `check_return_reason_id`."""

import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from openclear.auth.deps import require_permission
from openclear.db.session import get_db
from openclear.db.tenancy import TenantContext
from openclear.services import ach_return_reason_service, check_return_reason_service

router = APIRouter(prefix="/return-reasons", tags=["return-reasons"])
_read = require_permission("exception:read")


class CheckReturnReasonRead(BaseModel):
    id: uuid.UUID
    reason_text: str
    # The bank's own code for it, if it set one.
    return_code: str | None


class AchReturnReasonRead(BaseModel):
    id: uuid.UUID
    reason_text: str
    # The bank's core posting code for the reversal; the NACHA R code, where the bank uses one.
    transaction_code: str | None


@router.get("/check", response_model=list[CheckReturnReasonRead])
def check_return_reasons(db: Session = Depends(get_db), ctx: TenantContext = Depends(_read)) -> list[CheckReturnReasonRead]:
    """The bank's reasons for returning a check, active ones only."""
    return [CheckReturnReasonRead(id=r.id, reason_text=r.reason_text, return_code=r.return_code)
            for r in check_return_reason_service.list_check_return_reasons(db, ctx.tenant_id)]


@router.get("/ach", response_model=list[AchReturnReasonRead])
def ach_return_reasons(db: Session = Depends(get_db), ctx: TenantContext = Depends(_read)) -> list[AchReturnReasonRead]:
    """The bank's reasons for returning an ACH debit, active ones only."""
    return [AchReturnReasonRead(id=r.id, reason_text=r.reason_text, transaction_code=r.transaction_code)
            for r in ach_return_reason_service.list_ach_return_reasons(db, ctx.tenant_id, active_only=True)]
