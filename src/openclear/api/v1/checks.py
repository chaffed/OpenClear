# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Teller positive pay (TELLER_PLAN.md TL-1, TL-2; services/teller_service.py): a teller
platform verifies a check before cash is paid at a branch, then records it cashed. Both need
`check:verify`, a bank-level permission no person holds by default (the "Teller platform"
security group, for the platform's API client)."""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from openclear.auth.deps import require_permission
from openclear.db.session import get_db
from openclear.db.tenancy import TenantContext
from openclear.domain.issued_item import IssuedItem
from openclear.services import teller_service

router = APIRouter(prefix="/checks", tags=["teller"])

Result = Literal["match", "not_on_positive_pay", "stopped", "already_paid", "voided", "not_issued", "stop_differs", "amount_differs",
                 "payee_differs", "stale", "customer_review", "payee_needed"]


class VerifyCheck(BaseModel):
    routing_number: str | None = Field(None, pattern=r"^\d{9}$", description="From the MICR line")
    account_number: str = Field(..., min_length=1, max_length=64, description="From the MICR line")
    check_number: str = Field(..., min_length=1, max_length=32, description="From the MICR line")
    amount: Decimal = Field(..., gt=0, max_digits=14, decimal_places=2)
    payee_name: str | None = Field(None, max_length=255, description="The payee as written on the check. Required when the customer "
                                                                     "gave a payee for the issued check: without it the answer is payee_needed")
    teller_id: str | None = Field(None, max_length=64, description="Who asked, as the teller platform identifies them")
    branch: str | None = Field(None, max_length=64)


class IssuedDetails(BaseModel):
    """What was issued, to explain a decline. Never sent for a match."""

    amount: Decimal
    payee_name: str
    issue_date: date


class Verification(BaseModel):
    verification_id: uuid.UUID
    result: Result = Field(..., description="match, or the first problem. Pay out only on match; otherwise follow the bank's procedure.")
    problems: list[Result] = Field(..., description="Every problem found, in the order the clearing rules found them")
    expires_at: datetime = Field(..., description="Record it cashed before this, or verify it again")
    issued: IssuedDetails | None = None


class CashCheck(BaseModel):
    verification_id: uuid.UUID
    amount: Decimal = Field(..., gt=0, max_digits=14, decimal_places=2, description="What was paid out: the amount verified")


class CashedCheck(BaseModel):
    verification_id: uuid.UUID
    paid_item_id: uuid.UUID
    exception_types: list[str] = Field(..., description="Empty when the check cleared as paid. Otherwise what went wrong between "
                                                        "verify and cash, such as a stop placed in between: the bank should follow up.")


@router.post("/verify", response_model=Verification)
def verify_check(payload: VerifyCheck, db: Session = Depends(get_db),
                 ctx: TenantContext = Depends(require_permission("check:verify"))) -> Verification:
    """Whether a check can be cashed at a branch: the clearing rules, run now, over this check.
    Pay out only on `match`. A match can be recorded cashed once, within 15 minutes."""
    verification = teller_service.verify(db, ctx.tenant_id, teller_service.VerifyRequest(**payload.model_dump()), user_id=ctx.user_id)
    issued = None
    if verification.result != teller_service.MATCH and verification.issued_item_id is not None:
        item = db.get(IssuedItem, verification.issued_item_id)
        issued = IssuedDetails(amount=item.amount, payee_name=item.payee_name, issue_date=item.issue_date)
    db.commit()
    return Verification(verification_id=verification.id, result=verification.result, problems=verification.problems,
                        expires_at=verification.expires_at, issued=issued)


@router.post("/cashed", response_model=CashedCheck, status_code=201,
             responses={404: {"description": "No such verification"},
                        409: {"description": "Declined, expired, or another amount; or already recorded, in which case `detail` is an "
                                             "object with `message`, `paid_item_id` and `cashed_at`, so a platform that lost the "
                                             "first answer can reconcile"}})
def record_cashed(payload: CashCheck, db: Session = Depends(get_db),
                  ctx: TenantContext = Depends(require_permission("check:verify"))) -> CashedCheck:
    """Records a verified check as cashed: it's presented as paid at once, so the same check
    presented anywhere later is a duplicate. Each verification can be recorded once."""
    try:
        cashed = teller_service.cash(db, ctx.tenant_id, payload.verification_id, payload.amount, user_id=ctx.user_id)
    except teller_service.TellerError as exc:
        db.rollback()
        detail = {"message": str(exc), **{k: (str(v) if isinstance(v, uuid.UUID) else v.isoformat() if hasattr(v, "isoformat") else v)
                                          for k, v in exc.details.items()}} if exc.details else str(exc)
        raise HTTPException(exc.status, detail) from None
    db.commit()
    return CashedCheck(verification_id=cashed.verification.id, paid_item_id=cashed.paid_item.id, exception_types=cashed.exception_types)
