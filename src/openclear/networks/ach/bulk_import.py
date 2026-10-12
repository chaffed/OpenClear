# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import uuid
from typing import Any

from sqlalchemy.orm import Session

from openclear.bulk_import.fields import RowFieldError, optional_str, parse_date, parse_decimal, require_str
from openclear.bulk_import.nacha import NachaEntry, transaction_type_for_code
from openclear.bulk_import.results import BulkFileRowResult
from openclear.domain.ach_transaction import AchTransactionSource, AchTransactionType
from openclear.networks.ach.ingestion import AchTransactionSubmission, ingest_ach_transaction
from openclear.services import account_service


def _submission_from_row(
    session: Session,
    tenant_id: uuid.UUID,
    row: dict[str, Any],
    *,
    auto_create_accounts: bool = False,
    scoped_customer_id: uuid.UUID | None = None,
) -> AchTransactionSubmission:
    """Delimited/Excel path. Expected (case/whitespace-insensitive) columns:
    account_number, originator_id, originator_name, receiver_id (optional), amount,
    transaction_type (debit/credit), sec_code, trace_number, effective_date, and an
    optional account_name used only when auto_create_accounts creates a new account."""
    account_number = require_str(row, "account_number")
    if auto_create_accounts:
        account = account_service.get_or_create_account_by_number(
            session, tenant_id, account_number, default_name=optional_str(row, "account_name"), customer_id=scoped_customer_id
        )
    else:
        account = account_service.get_account_by_number(
            session, tenant_id, account_number, customer_id=scoped_customer_id
        )
        if account is None:
            raise RowFieldError(f"No account found with account number {account_number!r}", "account_number")

    transaction_type_raw = require_str(row, "transaction_type").lower()
    if transaction_type_raw not in ("debit", "credit"):
        raise RowFieldError(f"'transaction_type' must be 'debit' or 'credit', got {transaction_type_raw!r}", "transaction_type")

    return AchTransactionSubmission(
        account_id=account.id,
        originator_id=require_str(row, "originator_id"),
        originator_name=require_str(row, "originator_name"),
        receiver_id=optional_str(row, "receiver_id"),
        amount=parse_decimal(row, "amount"),
        transaction_type=AchTransactionType(transaction_type_raw),
        sec_code=require_str(row, "sec_code").upper(),
        trace_number=require_str(row, "trace_number"),
        effective_date=parse_date(row, "effective_date"),
        source=AchTransactionSource.BULK_FILE,
    )


def _submission_from_nacha_entry(
    session: Session,
    tenant_id: uuid.UUID,
    entry: NachaEntry,
    *,
    auto_create_accounts: bool = False,
    scoped_customer_id: uuid.UUID | None = None,
) -> AchTransactionSubmission:
    """NACHA entries carry no account-name field, so an auto-created account here always
    falls back to the get_or_create_account_by_number placeholder name."""
    if auto_create_accounts:
        account = account_service.get_or_create_account_by_number(
            session, tenant_id, entry.dfi_account_number, customer_id=scoped_customer_id
        )
    else:
        account = account_service.get_account_by_number(
            session, tenant_id, entry.dfi_account_number, customer_id=scoped_customer_id
        )
        if account is None:
            raise RowFieldError(f"No account found with account number {entry.dfi_account_number!r}")

    transaction_type = AchTransactionType(transaction_type_for_code(entry.transaction_code))

    return AchTransactionSubmission(
        account_id=account.id,
        originator_id=entry.company_id,
        originator_name=entry.company_name,
        receiver_id=entry.individual_id,
        amount=entry.amount,
        transaction_type=transaction_type,
        sec_code=entry.sec_code,
        trace_number=entry.trace_number,
        effective_date=entry.effective_date,
        source=AchTransactionSource.BULK_FILE,
    )


def ingest_ach_row(
    session: Session,
    tenant_id: uuid.UUID,
    row: dict[str, Any],
    *,
    auto_create_accounts: bool = False,
    scoped_customer_id: uuid.UUID | None = None,
) -> uuid.UUID:
    """One delimited/Excel row: creates and evaluates the transaction, returning its id.
    The caller owns the transaction (see ingest_ach_rows, and services/bulk_import_service.py)."""
    submission = _submission_from_row(
        session, tenant_id, row, auto_create_accounts=auto_create_accounts, scoped_customer_id=scoped_customer_id
    )
    return ingest_ach_transaction(session, tenant_id, submission, scoped_customer_id=scoped_customer_id).id


def ingest_nacha_entry(
    session: Session,
    tenant_id: uuid.UUID,
    entry: NachaEntry,
    *,
    auto_create_accounts: bool = False,
    scoped_customer_id: uuid.UUID | None = None,
) -> uuid.UUID:
    """One NACHA entry, as ingest_ach_row does for a delimited row."""
    submission = _submission_from_nacha_entry(
        session, tenant_id, entry, auto_create_accounts=auto_create_accounts, scoped_customer_id=scoped_customer_id
    )
    return ingest_ach_transaction(session, tenant_id, submission, scoped_customer_id=scoped_customer_id).id


def nacha_entry_label(entry: NachaEntry) -> str:
    return f"entry at line {entry.line_number} (trace {entry.trace_number})"


def ingest_ach_rows(
    session: Session,
    tenant_id: uuid.UUID,
    rows: list[dict[str, Any]],
    *,
    auto_create_accounts: bool = False,
    scoped_customer_id: uuid.UUID | None = None,
) -> list[BulkFileRowResult]:
    """Entry point for delimited/Excel ACH bulk uploads. One DB transaction per row, same
    isolation pattern as ingest_ach_transactions_bulk, so a single bad row doesn't roll
    back the whole file. auto_create_accounts creates a new account for any account_number
    not already on file instead of failing that row."""
    results: list[BulkFileRowResult] = []
    for index, row in enumerate(rows):
        row_label = f"row {index + 2}"  # +2: 1-based, plus the header row itself
        try:
            txn_id = ingest_ach_row(
                session, tenant_id, row, auto_create_accounts=auto_create_accounts, scoped_customer_id=scoped_customer_id
            )
            session.commit()
            results.append(BulkFileRowResult(row_label=row_label, success=True, created_id=txn_id))
        except Exception as exc:  # noqa: BLE001 — isolate row failures in a bulk file
            session.rollback()
            results.append(BulkFileRowResult(row_label=row_label, success=False, error=str(exc)))
    return results


def ingest_nacha_entries(
    session: Session,
    tenant_id: uuid.UUID,
    entries: list[NachaEntry],
    *,
    auto_create_accounts: bool = False,
    scoped_customer_id: uuid.UUID | None = None,
) -> list[BulkFileRowResult]:
    """Entry point for NACHA file ACH bulk uploads — same per-entry isolation as
    ingest_ach_rows, just sourced from bulk_import.nacha.parse_nacha_file() instead of a
    delimited/Excel file."""
    results: list[BulkFileRowResult] = []
    for entry in entries:
        row_label = nacha_entry_label(entry)
        try:
            txn_id = ingest_nacha_entry(
                session, tenant_id, entry, auto_create_accounts=auto_create_accounts, scoped_customer_id=scoped_customer_id
            )
            session.commit()
            results.append(BulkFileRowResult(row_label=row_label, success=True, created_id=txn_id))
        except Exception as exc:  # noqa: BLE001 — isolate row failures in a bulk file
            session.rollback()
            results.append(BulkFileRowResult(row_label=row_label, success=False, error=str(exc)))
    return results
