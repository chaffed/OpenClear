# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy.orm import Session

from openclear.domain.exception_item import ExceptionItem
from openclear.domain.issued_item import IssuedItem, IssuedItemStatus
from openclear.domain.paid_item import PaidItem, PaidItemMatchStatus, PaidItemSettlementStatus, PaidItemSource
from openclear.ml.predict import score_exception
from openclear.networks.check.adapter import CheckAdapter
from openclear.repositories.account_repo import AccountRepository
from openclear.services import auto_disposition_service, notification_service, webhook_service

_adapter = CheckAdapter()


@dataclass(frozen=True, slots=True)
class PaidItemSubmission:
    account_id: uuid.UUID
    check_number: str
    presented_amount: Decimal
    presented_date: date
    source: PaidItemSource = PaidItemSource.API


@dataclass(frozen=True, slots=True)
class BulkRowResult:
    index: int
    success: bool
    paid_item_id: uuid.UUID | None = None
    match_status: str | None = None
    error: str | None = None


def ingest_paid_item(
    session: Session, tenant_id: uuid.UUID, submission: PaidItemSubmission, *, scoped_customer_id: uuid.UUID | None = None
) -> PaidItem:
    """Creates a paid_item, runs it through the check matching engine, and finalizes its
    status (plus the linked issued_item's status on a clean match, or an exception_item
    on a mismatch). Caller commits — see ingest_paid_items_bulk for the per-row-transaction
    pattern bulk file ingestion needs. `scoped_customer_id` is the caller's own customer
    scope (None for tenant-wide staff) — the account is resolved through it so a
    customer-scoped caller referencing another customer's account_id gets a clean
    ValueError, and so customer_id can be denormalized onto the new row."""
    account = AccountRepository(session, tenant_id, scoped_customer_id).get(submission.account_id)
    if account is None:
        raise ValueError("Account not found")

    paid_item = PaidItem(
        tenant_id=tenant_id,
        account_id=submission.account_id,
        customer_id=account.customer_id,
        check_number=submission.check_number,
        presented_amount=submission.presented_amount,
        presented_date=submission.presented_date,
        source=submission.source,
    )
    session.add(paid_item)
    session.flush()

    result = _adapter.evaluate(session, paid_item)
    paid_item.matched_issued_item_id = result.related_reference_id

    if result.matched:
        paid_item.match_status = PaidItemMatchStatus.MATCHED
        paid_item.settlement_status = PaidItemSettlementStatus.PAID
        if result.related_reference_id:
            issued_item = session.get(IssuedItem, result.related_reference_id)
            issued_item.status = IssuedItemStatus.PAID
    else:
        paid_item.match_status = PaidItemMatchStatus.EXCEPTION
        deadline = auto_disposition_service.compute_decision_deadline(session, tenant_id, paid_item.customer_id, "check")
        exception_item = ExceptionItem(
            tenant_id=tenant_id,
            network_code="check",
            customer_id=paid_item.customer_id,
            source_item_id=paid_item.id,
            related_reference_id=result.related_reference_id,
            exception_types=",".join(result.exception_types),
            decision_deadline=deadline,
        )
        session.add(exception_item)
        session.flush()
        score_exception(session, exception_item)
        notification_service.notify_exception_created(session, exception_item)
        webhook_service.emit(session, tenant_id, "exception.created", {
            "exception_id": exception_item.id, "network": "check", "customer_id": exception_item.customer_id,
        })

    session.flush()
    return paid_item


def ingest_paid_items_bulk(
    session: Session, tenant_id: uuid.UUID, submissions: list[PaidItemSubmission], *, scoped_customer_id: uuid.UUID | None = None
) -> list[BulkRowResult]:
    """One commit per row so a single bad row (bad FK, constraint violation) doesn't roll
    back the whole file (see docs/ARCHITECTURE.md, "Bulk ingestion")."""
    results: list[BulkRowResult] = []
    for index, submission in enumerate(submissions):
        try:
            paid_item = ingest_paid_item(session, tenant_id, submission, scoped_customer_id=scoped_customer_id)
            session.commit()
            results.append(
                BulkRowResult(
                    index=index,
                    success=True,
                    paid_item_id=paid_item.id,
                    match_status=paid_item.match_status.value,
                )
            )
        except Exception as exc:  # noqa: BLE001 — bulk ingestion must isolate row failures
            session.rollback()
            results.append(BulkRowResult(index=index, success=False, error=str(exc)))
    return results
