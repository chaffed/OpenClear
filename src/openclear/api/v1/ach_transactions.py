# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from openclear.auth.deps import require_permission
from openclear.db.session import get_db
from openclear.db.tenancy import TenantContext
from openclear.networks.ach.ingestion import AchTransactionSubmission, ingest_ach_transaction, ingest_ach_transactions_bulk
from openclear.repositories.ach_transaction_repo import AchTransactionRepository
from openclear.schemas.ach import AchTransactionCreate, AchTransactionRead
from openclear.schemas.common import BulkRowResultOut, BulkSubmitResponse
from openclear.services import audit_log_service

router = APIRouter(prefix="/ach/transactions", tags=["ach-transactions"])


def _to_submission(payload: AchTransactionCreate) -> AchTransactionSubmission:
    return AchTransactionSubmission(**payload.model_dump())


@router.post("", response_model=AchTransactionRead, status_code=status.HTTP_201_CREATED)
def create_ach_transaction(
    payload: AchTransactionCreate,
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_permission("ach_transaction:write")),
) -> AchTransactionRead:
    try:
        txn = ingest_ach_transaction(db, ctx.tenant_id, _to_submission(payload), scoped_customer_id=ctx.customer_id)
    except ValueError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from None
    audit_log_service.record_action(
        db,
        ctx.tenant_id,
        actor_user_id=ctx.user_id,
        channel="api",
        action="ach_transaction.create",
        summary=f"Submitted ACH {txn.transaction_type.value} of {txn.amount} from {txn.originator_name}",
        resource_type="ach_transaction",
        resource_id=txn.id,
    )
    db.commit()
    return AchTransactionRead.model_validate(txn)


@router.post("/bulk", response_model=BulkSubmitResponse)
def create_ach_transactions_bulk(
    payload: list[AchTransactionCreate],
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_permission("ach_transaction:write")),
) -> BulkSubmitResponse:
    submissions = [_to_submission(row) for row in payload]
    results = ingest_ach_transactions_bulk(db, ctx.tenant_id, submissions, scoped_customer_id=ctx.customer_id)
    for r in results:
        if not r.success:
            continue
        audit_log_service.record_action(
            db,
            ctx.tenant_id,
            actor_user_id=ctx.user_id,
            channel="api",
            action="ach_transaction.create",
            summary=f"ACH transaction created via bulk API (row {r.index})",
            resource_type="ach_transaction",
            resource_id=r.ach_transaction_id,
        )
    db.commit()
    out = [
        BulkRowResultOut(
            index=r.index,
            success=r.success,
            id=str(r.ach_transaction_id) if r.ach_transaction_id else None,
            status=r.match_status,
            error=r.error,
        )
        for r in results
    ]
    return BulkSubmitResponse(
        total=len(out), succeeded=sum(r.success for r in out), failed=sum(not r.success for r in out), results=out
    )


@router.get("", response_model=list[AchTransactionRead])
def list_ach_transactions(
    account_id: uuid.UUID | None = None,
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_permission("ach_transaction:read")),
) -> list[AchTransactionRead]:
    txns = AchTransactionRepository(db, ctx.tenant_id, ctx.customer_id).list(account_id=account_id)
    return [AchTransactionRead.model_validate(t) for t in txns]


@router.get("/{transaction_id}", response_model=AchTransactionRead)
def get_ach_transaction(
    transaction_id: uuid.UUID,
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_permission("ach_transaction:read")),
) -> AchTransactionRead:
    txn = AchTransactionRepository(db, ctx.tenant_id, ctx.customer_id).get(transaction_id)
    if txn is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "ACH transaction not found")
    return AchTransactionRead.model_validate(txn)
