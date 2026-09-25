# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy.orm import Session

from openclear.domain.ach_transaction import (
    AchMatchStatus,
    AchSettlementStatus,
    AchTransaction,
    AchTransactionSource,
    AchTransactionType,
)
from openclear.domain.exception_item import ExceptionItem
from openclear.ml.predict import score_exception
from openclear.networks.ach.adapter import AchAdapter
from openclear.repositories.account_repo import AccountRepository
from openclear.services import auto_disposition_service, notification_service

_adapter = AchAdapter()


@dataclass(frozen=True, slots=True)
class AchTransactionSubmission:
    account_id: uuid.UUID
    originator_id: str
    originator_name: str
    amount: Decimal
    transaction_type: AchTransactionType
    sec_code: str
    trace_number: str
    effective_date: date
    receiver_id: str | None = None
    source: AchTransactionSource = AchTransactionSource.API


@dataclass(frozen=True, slots=True)
class BulkRowResult:
    index: int
    success: bool
    ach_transaction_id: uuid.UUID | None = None
    match_status: str | None = None
    error: str | None = None


def ingest_ach_transaction(
    session: Session, tenant_id: uuid.UUID, submission: AchTransactionSubmission, *, scoped_customer_id: uuid.UUID | None = None
) -> AchTransaction:
    """`scoped_customer_id` is the caller's own customer scope (None for tenant-wide
    staff) — the account is resolved through it so a customer-scoped caller referencing
    another customer's account_id gets a clean ValueError, and so customer_id can be
    denormalized onto the new row."""
    account = AccountRepository(session, tenant_id, scoped_customer_id).get(submission.account_id)
    if account is None:
        raise ValueError("Account not found")

    txn = AchTransaction(
        tenant_id=tenant_id,
        account_id=submission.account_id,
        customer_id=account.customer_id,
        originator_id=submission.originator_id,
        originator_name=submission.originator_name,
        receiver_id=submission.receiver_id,
        amount=submission.amount,
        transaction_type=submission.transaction_type,
        sec_code=submission.sec_code,
        trace_number=submission.trace_number,
        effective_date=submission.effective_date,
        source=submission.source,
    )
    session.add(txn)
    session.flush()

    result = _adapter.evaluate(session, txn)

    if result.matched:
        txn.match_status = AchMatchStatus.MATCHED
        txn.settlement_status = AchSettlementStatus.PAID
    else:
        txn.match_status = AchMatchStatus.EXCEPTION
        deadline = auto_disposition_service.compute_decision_deadline(session, tenant_id, txn.customer_id, "ach")
        exception_item = ExceptionItem(
            tenant_id=tenant_id,
            network_code="ach",
            customer_id=txn.customer_id,
            source_item_id=txn.id,
            related_reference_id=result.related_reference_id,
            exception_types=",".join(result.exception_types),
            decision_deadline=deadline,
        )
        session.add(exception_item)
        session.flush()
        score_exception(session, exception_item)
        notification_service.notify_exception_created(session, exception_item)

    session.flush()
    return txn


def ingest_ach_transactions_bulk(
    session: Session, tenant_id: uuid.UUID, submissions: list[AchTransactionSubmission], *, scoped_customer_id: uuid.UUID | None = None
) -> list[BulkRowResult]:
    results: list[BulkRowResult] = []
    for index, submission in enumerate(submissions):
        try:
            txn = ingest_ach_transaction(session, tenant_id, submission, scoped_customer_id=scoped_customer_id)
            session.commit()
            results.append(
                BulkRowResult(index=index, success=True, ach_transaction_id=txn.id, match_status=txn.match_status.value)
            )
        except Exception as exc:  # noqa: BLE001 — isolate row failures in a bulk file
            session.rollback()
            results.append(BulkRowResult(index=index, success=False, error=str(exc)))
    return results
