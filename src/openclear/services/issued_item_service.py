# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from openclear.bulk_import.fields import RowFieldError, optional_str, parse_date, parse_decimal, require_str
from openclear.bulk_import.results import BulkFileRowResult
from openclear.domain.issued_item import IssuedItem, IssuedItemSource, IssuedItemStatus
from openclear.repositories.account_repo import AccountRepository
from openclear.repositories.issued_item_repo import IssuedItemRepository
from openclear.services import account_service


@dataclass(frozen=True, slots=True)
class IssuedItemInput:
    account_id: uuid.UUID
    check_number: str
    amount: Decimal
    payee_name: str
    issue_date: date


@dataclass(frozen=True, slots=True)
class BulkRowResult:
    index: int
    success: bool
    issued_item_id: uuid.UUID | None = None
    error: str | None = None


def create_issued_item(
    session: Session,
    tenant_id: uuid.UUID,
    data: IssuedItemInput,
    *,
    submitted_by_user_id: uuid.UUID | None,
    source: IssuedItemSource = IssuedItemSource.API,
    scoped_customer_id: uuid.UUID | None = None,
) -> IssuedItem:
    """`scoped_customer_id` is the caller's own customer scope (None for tenant-wide
    staff) — the account is resolved through it (not a raw session.get) so a
    customer-scoped caller referencing another customer's account_id gets a clean
    ValueError instead of silently succeeding, and so customer_id can be denormalized
    from the account onto the new row (see domain/issued_item.py)."""
    account = AccountRepository(session, tenant_id, scoped_customer_id).get(data.account_id)
    if account is None:
        raise ValueError("Account not found")

    repo = IssuedItemRepository(session, tenant_id)
    item = IssuedItem(
        account_id=data.account_id,
        customer_id=account.customer_id,
        check_number=data.check_number,
        amount=data.amount,
        payee_name=data.payee_name,
        issue_date=data.issue_date,
        source=source,
        submitted_by_user_id=submitted_by_user_id,
    )
    repo.add(item)
    session.flush()
    return item


def create_issued_items_bulk(
    session: Session,
    tenant_id: uuid.UUID,
    items: list[IssuedItemInput],
    *,
    submitted_by_user_id: uuid.UUID | None,
    scoped_customer_id: uuid.UUID | None = None,
) -> list[BulkRowResult]:
    results: list[BulkRowResult] = []
    for index, data in enumerate(items):
        try:
            item = create_issued_item(
                session,
                tenant_id,
                data,
                submitted_by_user_id=submitted_by_user_id,
                source=IssuedItemSource.BULK_FILE,
                scoped_customer_id=scoped_customer_id,
            )
            session.commit()
            results.append(BulkRowResult(index=index, success=True, issued_item_id=item.id))
        except Exception as exc:  # noqa: BLE001 — isolate row failures in a bulk file
            session.rollback()
            results.append(BulkRowResult(index=index, success=False, error=str(exc)))
    return results


class CantVoid(ValueError):
    """The message is fit to show."""


def void_issued_item(
    session: Session, tenant_id: uuid.UUID, item_id: uuid.UUID, reason: str, *, scoped_customer_id: uuid.UUID | None = None
) -> IssuedItem | None:
    """Voids an issued check; None if it isn't found. A paid check can't be voided (it has
    cleared), wherever the request comes from (FULL_REVIEW_PLAN.md F4)."""
    repo = IssuedItemRepository(session, tenant_id, scoped_customer_id)
    item = repo.get(item_id)
    if item is None:
        return None
    if item.status == IssuedItemStatus.PAID:
        raise CantVoid(f"Check {item.check_number} has already been paid, so it can't be voided.")
    item.status = IssuedItemStatus.VOIDED
    item.void_reason = reason
    item.voided_at = datetime.now(timezone.utc)
    session.flush()
    return item


def list_issued_items(
    session: Session,
    tenant_id: uuid.UUID,
    *,
    status: IssuedItemStatus | None = None,
    account_id: uuid.UUID | None = None,
    customer_id: uuid.UUID | None = None,
    limit: int | None = None,
    offset: int | None = None,
    order_by: Any = None,
) -> list[IssuedItem]:
    repo = IssuedItemRepository(session, tenant_id, customer_id)
    return repo.list(status=status, account_id=account_id, limit=limit, offset=offset, order_by=order_by)


def _issued_item_input_from_row(
    session: Session,
    tenant_id: uuid.UUID,
    row: dict[str, Any],
    *,
    auto_create_accounts: bool = False,
    scoped_customer_id: uuid.UUID | None = None,
) -> IssuedItemInput:
    """Raises RowFieldError on any missing/invalid field or unrecognized account_number
    — callers (create_issued_items_from_rows) catch this per-row so one bad row doesn't
    fail the whole file. Expected (case/whitespace-insensitive) columns: account_number,
    check_number, amount, payee_name, issue_date, and an optional account_name used only
    when auto_create_accounts creates a new account for a number this tenant doesn't
    already have. `scoped_customer_id` restricts both the lookup and any auto-created
    account to that one customer, same as every other customer-scoped bulk import."""
    account_number = require_str(row, "account_number")
    if auto_create_accounts:
        account = account_service.get_or_create_account_by_number(
            session,
            tenant_id,
            account_number,
            default_name=optional_str(row, "account_name"),
            customer_id=scoped_customer_id,
        )
    else:
        account = account_service.get_account_by_number(
            session, tenant_id, account_number, customer_id=scoped_customer_id
        )
        if account is None:
            raise RowFieldError(f"No account found with account number {account_number!r}", "account_number")

    return IssuedItemInput(
        account_id=account.id,
        check_number=require_str(row, "check_number"),
        amount=parse_decimal(row, "amount"),
        payee_name=require_str(row, "payee_name"),
        issue_date=parse_date(row, "issue_date"),
    )


def create_issued_item_from_row(
    session: Session,
    tenant_id: uuid.UUID,
    row: dict[str, Any],
    *,
    submitted_by_user_id: uuid.UUID | None,
    auto_create_accounts: bool = False,
    scoped_customer_id: uuid.UUID | None = None,
) -> uuid.UUID:
    """One delimited/Excel row. The caller owns the transaction."""
    data = _issued_item_input_from_row(
        session, tenant_id, row, auto_create_accounts=auto_create_accounts, scoped_customer_id=scoped_customer_id
    )
    item = create_issued_item(
        session, tenant_id, data, submitted_by_user_id=submitted_by_user_id, source=IssuedItemSource.BULK_FILE,
        scoped_customer_id=scoped_customer_id,
    )
    return item.id


def create_issued_item_from_layout_row(
    session: Session,
    tenant_id: uuid.UUID,
    row: dict[str, Any],
    *,
    submitted_by_user_id: uuid.UUID | None,
    auto_create_accounts: bool = False,
    scoped_customer_id: uuid.UUID | None = None,
) -> uuid.UUID:
    """One item read by a layout (bulk_import/layouts.convert). A void line voids the check
    already issued with that number on that account, or records it as issued and voided
    if it isn't on file yet, so a later presentment of it is still caught."""
    from openclear.repositories.issued_item_repo import IssuedItemRepository

    data = _issued_item_input_from_row(
        session, tenant_id, row, auto_create_accounts=auto_create_accounts, scoped_customer_id=scoped_customer_id
    )
    if row.get("void"):
        existing = IssuedItemRepository(session, tenant_id, scoped_customer_id).list(account_id=data.account_id,
                                                                                    check_number=data.check_number)
        item = existing[0] if existing else create_issued_item(
            session, tenant_id, data, submitted_by_user_id=submitted_by_user_id, source=IssuedItemSource.BULK_FILE,
            scoped_customer_id=scoped_customer_id,
        )
        if item.status != IssuedItemStatus.VOIDED:
            void_issued_item(session, tenant_id, item.id, "Voided by an issue file", scoped_customer_id=scoped_customer_id)
        return item.id
    item = create_issued_item(
        session, tenant_id, data, submitted_by_user_id=submitted_by_user_id, source=IssuedItemSource.BULK_FILE,
        scoped_customer_id=scoped_customer_id,
    )
    return item.id


def create_issued_items_from_rows(
    session: Session,
    tenant_id: uuid.UUID,
    rows: list[dict[str, Any]],
    *,
    submitted_by_user_id: uuid.UUID | None,
    auto_create_accounts: bool = False,
    scoped_customer_id: uuid.UUID | None = None,
) -> list[BulkFileRowResult]:
    """Entry point for delimited/Excel bulk uploads (see web/routers/issued_items.py) —
    each row carries its own account_number (resolved to this tenant's account), unlike
    the JSON API's /issued-items/bulk which takes a single tenant-wide account per item
    already as a UUID. One DB transaction per row, same isolation pattern as
    create_issued_items_bulk, so a single bad row doesn't roll back the whole file.
    auto_create_accounts creates a new account for any account_number not already on file
    instead of failing that row — safe across repeated account numbers within the same
    file since each row commits before the next one's lookup runs."""
    results: list[BulkFileRowResult] = []
    for index, row in enumerate(rows):
        row_label = f"row {index + 2}"  # +2: 1-based, plus the header row itself
        try:
            item_id = create_issued_item_from_row(
                session, tenant_id, row, submitted_by_user_id=submitted_by_user_id,
                auto_create_accounts=auto_create_accounts, scoped_customer_id=scoped_customer_id,
            )
            session.commit()
            results.append(BulkFileRowResult(row_label=row_label, success=True, created_id=item_id))
        except RowFieldError as exc:
            session.rollback()
            results.append(BulkFileRowResult(row_label=row_label, success=False, error=str(exc)))
        except Exception as exc:  # noqa: BLE001 — isolate row failures (e.g. duplicate check number) in a bulk file
            session.rollback()
            results.append(BulkFileRowResult(row_label=row_label, success=False, error=str(exc)))
    return results
