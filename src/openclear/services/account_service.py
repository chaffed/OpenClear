# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from openclear.bulk_import.fields import RowFieldError, optional_str, require_str
from openclear.bulk_import.results import BulkFileRowResult
from openclear.domain.account import Account, AchDebitBlockMode
from openclear.repositories.account_repo import AccountRepository
from openclear.services import customer_service


@dataclass(frozen=True, slots=True)
class AccountInput:
    account_number: str
    name: str
    # None = unassigned "house" account, visible only to tenant-wide staff — see
    # domain/tenant_membership.py::customer_id and CustomerScopedRepository.
    customer_id: uuid.UUID | None = None
    ach_debit_block_mode: AchDebitBlockMode | None = None
    # A customer's own internal reference for this account — optional, resolved as a
    # fallback by get_account_by_number below so bulk files can use it instead of the
    # real account_number.
    external_account_id: str | None = None


def create_account(session: Session, tenant_id: uuid.UUID, data: AccountInput) -> Account:
    repo = AccountRepository(session, tenant_id)
    account = Account(
        account_number=data.account_number, name=data.name, customer_id=data.customer_id,
        external_account_id=data.external_account_id,
    )
    if data.ach_debit_block_mode is not None:
        account.ach_debit_block_mode = data.ach_debit_block_mode
    repo.add(account)
    session.flush()
    return account


def list_accounts(
    session: Session,
    tenant_id: uuid.UUID,
    *,
    customer_id: uuid.UUID | None = None,
    limit: int | None = None,
    offset: int | None = None,
    order_by: Any = None,
) -> list[Account]:
    return AccountRepository(session, tenant_id, customer_id).list(limit=limit, offset=offset, order_by=order_by)


class ServiceEnded(RowFieldError):
    """A file names an account whose customer's positive pay has ended (services/
    customer_offboarding_service.py): its row is refused, so the bank sees the core still sends it."""


def _refuse_ended(session: Session, account: Account | None) -> Account | None:
    from openclear.domain.customer import Customer
    from openclear.services import customer_offboarding_service as offboarding

    if account is not None and account.customer_id is not None:
        customer = session.get(Customer, account.customer_id)
        if offboarding.ended(customer):  # from midnight at the bank on its last day, not from the daily run (RV-D5)
            raise ServiceEnded(f"Positive pay ended for {customer.name}, this account's customer, after "
                               f"{offboarding.last_service_day(session, customer):%Y-%m-%d}; its items are no longer read.",
                               "account_number")
    return account


def find_account_by_number(session: Session, tenant_id: uuid.UUID, account_number: str, *,
                           customer_id: uuid.UUID | None = None) -> Account | None:
    """get_account_by_number without the refusal of an ended customer's account: for a teller
    asking about a check, or a replay over history, where the caller decides what ended service
    means (REVIEW_1_33_PLAN.md T4)."""
    repo = AccountRepository(session, tenant_id, customer_id)
    matches = repo.list(account_number=account_number) or repo.list(external_account_id=account_number)
    return matches[0] if matches else None


def get_account_by_number(
    session: Session, tenant_id: uuid.UUID, account_number: str, *, customer_id: uuid.UUID | None = None
) -> Account | None:
    """Used by bulk file imports (issued items, ACH) to resolve a human-readable account
    reference in an uploaded row/entry to this tenant's internal account id — files never
    carry our UUIDs, only a reference a user would actually recognize. Tries the real
    account_number first (so an existing file that already matches by number resolves
    exactly as it always has, with zero risk of an external ID accidentally shadowing
    it), then falls back to external_account_id — a customer's own internal reference —
    if nothing matched. `customer_id` scopes the lookup exactly like any other
    CustomerScopedRepository read — a customer-scoped bulk upload can only match its own
    customer's accounts."""
    repo = AccountRepository(session, tenant_id, customer_id)
    matches = repo.list(account_number=account_number)
    if matches:
        return _refuse_ended(session, matches[0])
    matches = repo.list(external_account_id=account_number)
    return _refuse_ended(session, matches[0]) if matches else None


def get_or_create_account_by_number(
    session: Session,
    tenant_id: uuid.UUID,
    account_number: str,
    *,
    default_name: str | None = None,
    customer_id: uuid.UUID | None = None,
) -> Account:
    """Used by bulk file imports when the "create missing accounts" option is checked —
    same lookup as get_account_by_number, but creates a new account instead of returning
    None when the file references an account number this tenant doesn't have yet. Files
    rarely carry an account name, so callers may pass one (e.g. from an optional
    "account_name" column) and it otherwise falls back to a placeholder derived from the
    number itself. A newly-created account inherits `customer_id` from the caller's own
    scope, so a customer-scoped upload can never auto-create an unassigned or
    other-customer account."""
    account = get_account_by_number(session, tenant_id, account_number, customer_id=customer_id)
    if account is not None:
        return account
    return create_account(
        session,
        tenant_id,
        AccountInput(
            account_number=account_number,
            name=default_name or f"Account {account_number}",
            customer_id=customer_id,
        ),
    )


def set_ach_debit_block_mode(session: Session, tenant_id: uuid.UUID, account_id: uuid.UUID, mode: AchDebitBlockMode) -> Account | None:
    repo = AccountRepository(session, tenant_id)
    account = repo.get(account_id)
    if account is None:
        return None
    account.ach_debit_block_mode = mode
    session.flush()
    return account


def _account_input_from_row(
    session: Session, tenant_id: uuid.UUID, row: dict[str, Any], *, scoped_customer_id: uuid.UUID | None
) -> AccountInput:
    """Raises RowFieldError on any missing/invalid field. Expected (case/whitespace-
    insensitive) columns: account_number, name, customer_number (optional — blank means
    an unassigned "house" account), ach_debit_block_mode (optional), external_account_id
    (optional — a customer's own internal reference for this account, later usable in
    place of account_number when submitting other bulk files).

    When the upload itself is customer-scoped (scoped_customer_id is not None — see
    web/routers/accounts.py), every row is stamped with that customer regardless of
    whether a customer_number column is present; if one IS present it must name that same
    customer, since a customer-scoped upload can never create another customer's (or an
    unassigned) account."""
    account_number = require_str(row, "account_number")
    name = require_str(row, "name")
    customer_number = optional_str(row, "customer_number")

    if scoped_customer_id is not None:
        if customer_number:
            customer = customer_service.get_customer_by_number(session, tenant_id, customer_number)
            if customer is None or customer.id != scoped_customer_id:
                raise RowFieldError(f"customer_number {customer_number!r} does not match your assigned customer")
        customer_id = scoped_customer_id
    elif customer_number:
        customer = customer_service.get_customer_by_number(session, tenant_id, customer_number)
        if customer is None:
            raise RowFieldError(f"No customer found with customer number {customer_number!r}")
        customer_id = customer.id
    else:
        customer_id = None

    mode_raw = optional_str(row, "ach_debit_block_mode")
    ach_debit_block_mode = None
    if mode_raw:
        try:
            ach_debit_block_mode = AchDebitBlockMode(mode_raw.strip().lower())
        except ValueError:
            valid = ", ".join(m.value for m in AchDebitBlockMode)
            raise RowFieldError(f"'ach_debit_block_mode' must be one of {valid}, got {mode_raw!r}") from None

    return AccountInput(
        account_number=account_number, name=name, customer_id=customer_id, ach_debit_block_mode=ach_debit_block_mode,
        external_account_id=optional_str(row, "external_account_id"),
    )


def create_account_from_row(
    session: Session, tenant_id: uuid.UUID, row: dict[str, Any], *, scoped_customer_id: uuid.UUID | None = None
) -> uuid.UUID:
    """One delimited/Excel row. The caller owns the transaction. A `cf_<key>` column sets that
    custom field (plans/CUSTOM_FIELDS_PLAN.md CF-D6), by the bank's staff only; a blank cell leaves
    it empty."""
    from openclear.services import custom_field_service

    account = create_account(session, tenant_id, _account_input_from_row(session, tenant_id, row, scoped_customer_id=scoped_customer_id))
    values = {str(k)[3:].strip(): custom_field_service.cell_text(v) for k, v in row.items() if str(k).lower().startswith("cf_")}
    values = {k: v for k, v in values.items() if v}
    if values:
        if scoped_customer_id is not None:
            raise RowFieldError("Only the bank's staff set custom fields; leave the cf_ columns out.")
        try:
            custom_field_service.set_values(session, tenant_id, "account", account.id, values, user_id=None, channel="web")
        except custom_field_service.CustomFieldError as exc:
            raise RowFieldError(str(exc)) from None
    return account.id


def create_accounts_from_rows(
    session: Session,
    tenant_id: uuid.UUID,
    rows: list[dict[str, Any]],
    *,
    scoped_customer_id: uuid.UUID | None = None,
) -> list[BulkFileRowResult]:
    """Entry point for the accounts bulk upload (web/routers/accounts.py) — same
    per-row-transaction isolation pattern as every other bulk import in this app, so one
    bad row doesn't roll back the whole file."""
    results: list[BulkFileRowResult] = []
    for index, row in enumerate(rows):
        row_label = f"row {index + 2}"  # +2: 1-based, plus the header row itself
        try:
            account_id = create_account_from_row(session, tenant_id, row, scoped_customer_id=scoped_customer_id)
            session.commit()
            results.append(BulkFileRowResult(row_label=row_label, success=True, created_id=account_id))
        except RowFieldError as exc:
            session.rollback()
            results.append(BulkFileRowResult(row_label=row_label, success=False, error=str(exc)))
        except Exception as exc:  # noqa: BLE001 — isolate row failures (e.g. duplicate account number) in a bulk file
            session.rollback()
            results.append(BulkFileRowResult(row_label=row_label, success=False, error=str(exc)))
    return results
