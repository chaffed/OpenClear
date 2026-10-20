# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Feeds known-fraud transactions into the ML training set — either by attaching a
training label to a check/ACH transaction that already exists in OpenClear (the high-value
case: something that cleared clean, or was live-decided wrong), or by entering details for
one that was never in OpenClear at all. Deliberately does NOT run the real matching rules
(CheckAdapter.evaluate()/AchAdapter.evaluate()) — a known-fraud transaction is exactly the
kind that can clear those rules cleanly (see networks/check/rules.py, networks/ach/rules.py
docstrings), so waiting for a real rule violation would defeat the point. Does reuse
build_features() unchanged, so a backfilled feature vector is computed identically to a
live-reviewed one, and never calls score_exception()/notification_service (this never
enters anyone's live queue — see ml/train.py::_labeled_decisions, which picks these
rows up through the same query as a live decision, no separate training path needed)."""

import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from openclear.bulk_import.fields import RowFieldError, optional_str, parse_date, parse_decimal, require_str
from openclear.bulk_import.results import BulkFileRowResult
from openclear.db.tenancy import TenantContext
from openclear.domain.ach_transaction import (
    AchMatchStatus,
    AchSettlementStatus,
    AchTransaction,
    AchTransactionSource,
    AchTransactionType,
)
from openclear.domain.decision import Decision, DecisionOutcome
from openclear.domain.exception_item import ExceptionItem, ExceptionItemSource, ExceptionStatus
from openclear.domain.paid_item import PaidItem, PaidItemMatchStatus, PaidItemSettlementStatus, PaidItemSource
from openclear.networks.registry import get_adapter
from openclear.repositories.account_repo import AccountRepository
from openclear.repositories.ach_return_reason_repo import AchReturnReasonRepository
from openclear.repositories.ach_transaction_repo import AchTransactionRepository
from openclear.repositories.decision_repo import DecisionRepository
from openclear.repositories.exception_repo import ExceptionRepository
from openclear.repositories.paid_item_repo import PaidItemRepository
from openclear.services import account_service


class FraudTrainingError(ValueError):
    """Any rejected submission (unknown source item, out of scope, already labeled,
    invalid ACH return reason, retracting a non-backfill/already-retracted exception) —
    the message is surfaced directly to the caller (web form / API error body)."""


@dataclass(frozen=True, slots=True)
class CheckFraudRawInput:
    account_id: uuid.UUID
    check_number: str
    presented_amount: Decimal
    presented_date: date


@dataclass(frozen=True, slots=True)
class AchFraudRawInput:
    account_id: uuid.UUID
    originator_id: str
    originator_name: str
    amount: Decimal
    transaction_type: AchTransactionType
    sec_code: str
    trace_number: str
    effective_date: date
    receiver_id: str | None = None


def _has_active_backfill_label(session: Session, tenant_id: uuid.UUID, source_item_id: uuid.UUID) -> bool:
    stmt = select(ExceptionItem.id).where(
        ExceptionItem.tenant_id == tenant_id,
        ExceptionItem.source_item_id == source_item_id,
        ExceptionItem.source == ExceptionItemSource.TRAINING_BACKFILL,
        ExceptionItem.retracted_at.is_(None),
    )
    return session.execute(stmt).first() is not None


def _is_already_live_decided(session: Session, tenant_id: uuid.UUID, source_item_id: uuid.UUID) -> bool:
    stmt = select(ExceptionItem.id).where(
        ExceptionItem.tenant_id == tenant_id,
        ExceptionItem.source_item_id == source_item_id,
        ExceptionItem.source == ExceptionItemSource.LIVE,
        ExceptionItem.status.in_([ExceptionStatus.PAY, ExceptionStatus.RETURN]),
    )
    return session.execute(stmt).first() is not None


def submit_check_fraud_example(
    session: Session,
    tenant_id: uuid.UUID,
    ctx: TenantContext,
    *,
    existing_paid_item_id: uuid.UUID | None = None,
    new_item: CheckFraudRawInput | None = None,
    reason_code: str,
    notes: str | None = None,
) -> ExceptionItem:
    """Exactly one of existing_paid_item_id/new_item must be given. `is_correction` is set
    automatically when the transaction was already live-decided PAY/RETURN — the model
    will then see two labels for a similar feature vector (the original live decision and
    this correction), accepted as normal label noise rather than blocked outright."""
    if (existing_paid_item_id is None) == (new_item is None):
        raise FraudTrainingError("Provide exactly one of an existing paid item or new transaction details")

    if existing_paid_item_id is not None:
        paid_item = PaidItemRepository(session, tenant_id, ctx.customer_id).get(existing_paid_item_id)
        if paid_item is None:
            raise FraudTrainingError("Paid item not found")
        if _has_active_backfill_label(session, tenant_id, paid_item.id):
            raise FraudTrainingError("This transaction already has an active fraud training label")
        is_correction = _is_already_live_decided(session, tenant_id, paid_item.id)
    else:
        account = AccountRepository(session, tenant_id, ctx.customer_id).get(new_item.account_id)
        if account is None:
            raise FraudTrainingError("Account not found")
        paid_item = PaidItem(
            account_id=new_item.account_id,
            customer_id=account.customer_id,
            check_number=new_item.check_number,
            presented_amount=new_item.presented_amount,
            presented_date=new_item.presented_date,
            source=PaidItemSource.TRAINING_BACKFILL,
            match_status=PaidItemMatchStatus.EXCEPTION,
        )
        PaidItemRepository(session, tenant_id, ctx.customer_id).add(paid_item)
        session.flush()
        is_correction = False

    paid_item.settlement_status = PaidItemSettlementStatus.RETURNED

    exception_item = ExceptionItem(
        network_code="check",
        customer_id=paid_item.customer_id,
        source_item_id=paid_item.id,
        exception_types="known_fraud",
        status=ExceptionStatus.RETURN,
        source=ExceptionItemSource.TRAINING_BACKFILL,
        is_correction=is_correction,
    )
    ExceptionRepository(session, tenant_id, ctx.customer_id).add(exception_item)
    session.flush()

    features = get_adapter("check").build_features(session, exception_item)

    decision = Decision(
        exception_item_id=exception_item.id,
        outcome=DecisionOutcome.RETURN,
        reason_code=reason_code,
        notes=notes,
        submitted_by_user_id=None,
        decided_by_user_id=ctx.user_id,
        features_json=features,
        locked_at=datetime.now(timezone.utc),  # a training label: final, and never sent anywhere
    )
    DecisionRepository(session, tenant_id).add(decision)
    session.flush()
    return exception_item


def submit_ach_fraud_example(
    session: Session,
    tenant_id: uuid.UUID,
    ctx: TenantContext,
    *,
    existing_ach_transaction_id: uuid.UUID | None = None,
    new_item: AchFraudRawInput | None = None,
    ach_return_reason_id: uuid.UUID,
    notes: str | None = None,
) -> ExceptionItem:
    """ACH mirror of submit_check_fraud_example. An ACH return must come from the
    tenant's own AchReturnReason catalog (not freeform text) — the same rule
    decision_service.py::decide() enforces for a live ACH return — so return_transaction_code
    and downstream WSUD-eligibility signals populate consistently either way."""
    if (existing_ach_transaction_id is None) == (new_item is None):
        raise FraudTrainingError("Provide exactly one of an existing ACH transaction or new transaction details")

    reason = AchReturnReasonRepository(session, tenant_id).get(ach_return_reason_id)
    if reason is None or not reason.is_active:
        raise FraudTrainingError("Invalid or inactive ACH return reason")

    if existing_ach_transaction_id is not None:
        txn = AchTransactionRepository(session, tenant_id, ctx.customer_id).get(existing_ach_transaction_id)
        if txn is None:
            raise FraudTrainingError("ACH transaction not found")
        if _has_active_backfill_label(session, tenant_id, txn.id):
            raise FraudTrainingError("This transaction already has an active fraud training label")
        is_correction = _is_already_live_decided(session, tenant_id, txn.id)
    else:
        account = AccountRepository(session, tenant_id, ctx.customer_id).get(new_item.account_id)
        if account is None:
            raise FraudTrainingError("Account not found")
        txn = AchTransaction(
            account_id=new_item.account_id,
            customer_id=account.customer_id,
            originator_id=new_item.originator_id,
            originator_name=new_item.originator_name,
            receiver_id=new_item.receiver_id,
            amount=new_item.amount,
            transaction_type=new_item.transaction_type,
            sec_code=new_item.sec_code,
            trace_number=new_item.trace_number,
            effective_date=new_item.effective_date,
            source=AchTransactionSource.TRAINING_BACKFILL,
            match_status=AchMatchStatus.EXCEPTION,
        )
        AchTransactionRepository(session, tenant_id, ctx.customer_id).add(txn)
        session.flush()
        is_correction = False

    txn.settlement_status = AchSettlementStatus.RETURNED

    exception_item = ExceptionItem(
        network_code="ach",
        customer_id=txn.customer_id,
        source_item_id=txn.id,
        exception_types="known_fraud",
        status=ExceptionStatus.RETURN,
        source=ExceptionItemSource.TRAINING_BACKFILL,
        is_correction=is_correction,
    )
    ExceptionRepository(session, tenant_id, ctx.customer_id).add(exception_item)
    session.flush()

    features = get_adapter("ach").build_features(session, exception_item)

    decision = Decision(
        exception_item_id=exception_item.id,
        outcome=DecisionOutcome.RETURN,
        reason_code=reason.reason_text,
        return_transaction_code=reason.transaction_code,
        notes=notes,
        submitted_by_user_id=None,
        decided_by_user_id=ctx.user_id,
        features_json=features,
        locked_at=datetime.now(timezone.utc),  # a training label: final, and never sent anywhere
    )
    DecisionRepository(session, tenant_id).add(decision)
    session.flush()
    return exception_item


def retract_fraud_example(
    session: Session, tenant_id: uuid.UUID, ctx: TenantContext, exception_id: uuid.UUID
) -> ExceptionItem | None:
    """One-way soft retraction — mirrors BulkUploadFile.backed_out_at's own "nothing
    supports undoing the undo" design. Only ever allowed on a TRAINING_BACKFILL exception:
    a real live decision is never retractable (see ml/train.py::_labeled_decisions,
    which excludes retracted rows from training)."""
    exception_item = ExceptionRepository(session, tenant_id, ctx.customer_id).get(exception_id)
    if exception_item is None:
        return None
    if exception_item.source != ExceptionItemSource.TRAINING_BACKFILL:
        raise FraudTrainingError("Only training-backfill examples can be retracted")
    if exception_item.retracted_at is not None:
        raise FraudTrainingError("Already retracted")
    exception_item.retracted_at = datetime.now(timezone.utc)
    exception_item.retracted_by_user_id = ctx.user_id
    session.flush()
    return exception_item


def _check_fraud_example_from_row(
    session: Session, tenant_id: uuid.UUID, ctx: TenantContext, row: dict[str, Any]
) -> tuple[uuid.UUID | None, CheckFraudRawInput | None, str, str | None]:
    """Expected columns: either `paid_item_id` (attach to an existing transaction), or
    `account_number`/`check_number`/`presented_amount`/`presented_date` (a new synthetic
    one) — plus `reason_code` and an optional `notes`, always."""
    reason_code = require_str(row, "reason_code")
    notes = optional_str(row, "notes")

    paid_item_id_raw = optional_str(row, "paid_item_id")
    if paid_item_id_raw:
        try:
            return uuid.UUID(paid_item_id_raw), None, reason_code, notes
        except ValueError:
            raise RowFieldError(f"'paid_item_id' is not a valid id: {paid_item_id_raw!r}") from None

    account_number = require_str(row, "account_number")
    account = account_service.get_account_by_number(session, tenant_id, account_number, customer_id=ctx.customer_id)
    if account is None:
        raise RowFieldError(f"No account found with account number {account_number!r}", "account_number")
    new_item = CheckFraudRawInput(
        account_id=account.id,
        check_number=require_str(row, "check_number"),
        presented_amount=parse_decimal(row, "presented_amount"),
        presented_date=parse_date(row, "presented_date"),
    )
    return None, new_item, reason_code, notes


def submit_check_fraud_examples_from_rows(
    session: Session, tenant_id: uuid.UUID, ctx: TenantContext, rows: list[dict[str, Any]]
) -> list[BulkFileRowResult]:
    """One DB transaction per row, same isolation pattern as every other bulk import in
    this app — a single bad row doesn't roll back the whole file."""
    results: list[BulkFileRowResult] = []
    for index, row in enumerate(rows):
        row_label = f"row {index + 2}"  # +2: 1-based, plus the header row itself
        try:
            existing_id, new_item, reason_code, notes = _check_fraud_example_from_row(session, tenant_id, ctx, row)
            exception_item = submit_check_fraud_example(
                session,
                tenant_id,
                ctx,
                existing_paid_item_id=existing_id,
                new_item=new_item,
                reason_code=reason_code,
                notes=notes,
            )
            session.commit()
            results.append(BulkFileRowResult(row_label=row_label, success=True, created_id=exception_item.id))
        except (RowFieldError, FraudTrainingError) as exc:
            session.rollback()
            results.append(BulkFileRowResult(row_label=row_label, success=False, error=str(exc)))
        except Exception as exc:  # noqa: BLE001 — isolate row failures in a bulk file
            session.rollback()
            results.append(BulkFileRowResult(row_label=row_label, success=False, error=str(exc)))
    return results


def _ach_fraud_example_from_row(
    session: Session, tenant_id: uuid.UUID, ctx: TenantContext, row: dict[str, Any]
) -> tuple[uuid.UUID | None, AchFraudRawInput | None, uuid.UUID, str | None]:
    """Expected columns: either `ach_transaction_id`, or `account_number`/
    `originator_id`/`originator_name`/`amount`/`transaction_type`/`sec_code`/
    `trace_number`/`effective_date` (optionally `receiver_id`) — plus a required
    `ach_return_reason_id` (from this tenant's catalog, /ui/ach-return-reasons) and an
    optional `notes`, always."""
    ach_return_reason_id_raw = require_str(row, "ach_return_reason_id")
    try:
        ach_return_reason_id = uuid.UUID(ach_return_reason_id_raw)
    except ValueError:
        raise RowFieldError(f"'ach_return_reason_id' is not a valid id: {ach_return_reason_id_raw!r}") from None
    notes = optional_str(row, "notes")

    ach_transaction_id_raw = optional_str(row, "ach_transaction_id")
    if ach_transaction_id_raw:
        try:
            return uuid.UUID(ach_transaction_id_raw), None, ach_return_reason_id, notes
        except ValueError:
            raise RowFieldError(f"'ach_transaction_id' is not a valid id: {ach_transaction_id_raw!r}") from None

    account_number = require_str(row, "account_number")
    account = account_service.get_account_by_number(session, tenant_id, account_number, customer_id=ctx.customer_id)
    if account is None:
        raise RowFieldError(f"No account found with account number {account_number!r}", "account_number")
    new_item = AchFraudRawInput(
        account_id=account.id,
        originator_id=require_str(row, "originator_id"),
        originator_name=require_str(row, "originator_name"),
        receiver_id=optional_str(row, "receiver_id"),
        amount=parse_decimal(row, "amount"),
        transaction_type=AchTransactionType(require_str(row, "transaction_type").lower()),
        sec_code=require_str(row, "sec_code").upper(),
        trace_number=require_str(row, "trace_number"),
        effective_date=parse_date(row, "effective_date"),
    )
    return None, new_item, ach_return_reason_id, notes


def submit_ach_fraud_examples_from_rows(
    session: Session, tenant_id: uuid.UUID, ctx: TenantContext, rows: list[dict[str, Any]]
) -> list[BulkFileRowResult]:
    results: list[BulkFileRowResult] = []
    for index, row in enumerate(rows):
        row_label = f"row {index + 2}"
        try:
            existing_id, new_item, ach_return_reason_id, notes = _ach_fraud_example_from_row(
                session, tenant_id, ctx, row
            )
            exception_item = submit_ach_fraud_example(
                session,
                tenant_id,
                ctx,
                existing_ach_transaction_id=existing_id,
                new_item=new_item,
                ach_return_reason_id=ach_return_reason_id,
                notes=notes,
            )
            session.commit()
            results.append(BulkFileRowResult(row_label=row_label, success=True, created_id=exception_item.id))
        except (RowFieldError, FraudTrainingError, ValueError) as exc:
            session.rollback()
            results.append(BulkFileRowResult(row_label=row_label, success=False, error=str(exc)))
        except Exception as exc:  # noqa: BLE001 — isolate row failures in a bulk file
            session.rollback()
            results.append(BulkFileRowResult(row_label=row_label, success=False, error=str(exc)))
    return results
