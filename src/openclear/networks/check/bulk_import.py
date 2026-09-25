# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Turns parsed ZIP+CSV manifest rows / X9.37 check items into paid_item + check_image
rows — the glue layer for the two bulk check-image import formats, mirroring
networks/ach/bulk_import.py's role for ACH's own two formats (delimited/Excel + NACHA).

Both formats create a NEW paid_item per check (running it through the existing matching
engine via ingest_paid_item) rather than only attaching an image to a paid_item that
already exists — a real image cash letter IS the presentment, not a follow-up step.

Neither format takes a customer_number column, unlike the accounts/users bulk imports:
this app resolves customer scoping the same way every other check/ACH bulk import that
references an EXISTING account does (issued_item_service, stop_payment_service,
ach_authorization_service, networks/ach/bulk_import.py) — customer_id always comes from
the resolved account itself, via scoped_customer_id constraining that resolution for a
customer-scoped uploader, never from a separate column. customer_number only exists on
the accounts/users bulk imports, where a customer identity is actually being assigned for
the first time, which isn't the case here."""

import uuid
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from openclear.bulk_import.fields import RowFieldError, optional_str, parse_date, parse_decimal, require_str
from openclear.bulk_import.images import UnsupportedImageError, normalize_and_split_image
from openclear.bulk_import.results import BulkFileRowResult
from openclear.bulk_import.x937 import X937CheckItem
from openclear.bulk_import.zip_import import find_image
from openclear.domain.paid_item import PaidItemSource
from openclear.networks.check.ingestion import PaidItemSubmission, ingest_paid_item
from openclear.networks.check.ocr_processing import create_check_image
from openclear.services import account_service


def _create_paid_item_and_images(
    session: Session,
    tenant_id: uuid.UUID,
    *,
    account_number: str,
    check_number: str,
    amount: Decimal,
    presented_date: date,
    front_bytes: bytes,
    back_bytes: bytes | None,
    image_filename: str,
    scoped_customer_id: uuid.UUID | None,
) -> uuid.UUID:
    account = account_service.get_account_by_number(session, tenant_id, account_number, customer_id=scoped_customer_id)
    if account is None:
        raise RowFieldError(f"No account found with account number {account_number!r}")

    front_png, back_png = normalize_and_split_image(front_bytes, image_filename, back_data=back_bytes)

    paid_item = ingest_paid_item(
        session,
        tenant_id,
        PaidItemSubmission(
            account_id=account.id,
            check_number=check_number,
            presented_amount=amount,
            presented_date=presented_date,
            source=PaidItemSource.BULK_FILE,
        ),
        scoped_customer_id=scoped_customer_id,
    )
    create_check_image(
        session, tenant_id, front_bytes=front_png, back_bytes=back_png, paid_item_id=paid_item.id, customer_id=account.customer_id
    )
    return paid_item.id


def _submission_from_zip_row(
    session: Session,
    tenant_id: uuid.UUID,
    row: dict[str, Any],
    images: dict[str, bytes],
    *,
    scoped_customer_id: uuid.UUID | None,
) -> uuid.UUID:
    """Expected (case/whitespace-insensitive) manifest columns: account_number,
    check_number, amount, presented_date, front_image_filename, back_image_filename
    (optional -- a multi-page TIFF referenced by front_image_filename is split into
    front/back automatically when this is absent)."""
    account_number = require_str(row, "account_number")
    check_number = require_str(row, "check_number")
    amount = parse_decimal(row, "amount")
    presented_date = parse_date(row, "presented_date")
    front_filename = require_str(row, "front_image_filename")
    back_filename = row.get("back_image_filename")
    back_filename = str(back_filename).strip() if back_filename else None

    front_bytes = find_image(images, front_filename)
    if front_bytes is None:
        raise RowFieldError(f"front_image_filename {front_filename!r} was not found in the zip")
    back_bytes = None
    if back_filename:
        back_bytes = find_image(images, back_filename)
        if back_bytes is None:
            raise RowFieldError(f"back_image_filename {back_filename!r} was not found in the zip")

    return _create_paid_item_and_images(
        session,
        tenant_id,
        account_number=account_number,
        check_number=check_number,
        amount=amount,
        presented_date=presented_date,
        front_bytes=front_bytes,
        back_bytes=back_bytes,
        image_filename=front_filename,
        scoped_customer_id=scoped_customer_id,
    )


def ingest_check_image_zip_rows(
    session: Session,
    tenant_id: uuid.UUID,
    rows: list[dict[str, Any]],
    images: dict[str, bytes],
    *,
    scoped_customer_id: uuid.UUID | None = None,
) -> list[BulkFileRowResult]:
    """Entry point for the ZIP+CSV bulk check-image format. One DB transaction per row,
    same isolation pattern as every other bulk importer in this app."""
    results: list[BulkFileRowResult] = []
    for index, row in enumerate(rows):
        row_label = f"row {index + 2}"  # +2: 1-based, plus the header row itself
        try:
            paid_item_id = _submission_from_zip_row(session, tenant_id, row, images, scoped_customer_id=scoped_customer_id)
            session.commit()
            results.append(BulkFileRowResult(row_label=row_label, success=True, created_id=paid_item_id))
        except (RowFieldError, UnsupportedImageError) as exc:
            session.rollback()
            results.append(BulkFileRowResult(row_label=row_label, success=False, error=str(exc)))
        except Exception as exc:  # noqa: BLE001 — isolate row failures in a bulk file
            session.rollback()
            results.append(BulkFileRowResult(row_label=row_label, success=False, error=str(exc)))
    return results


def _submission_from_tabular_row(
    session: Session,
    tenant_id: uuid.UUID,
    row: dict[str, Any],
    *,
    auto_create_accounts: bool = False,
    scoped_customer_id: uuid.UUID | None = None,
) -> uuid.UUID:
    """Checks presented for payment as plain delimited data, with **no image** attached
    — distinct from the ZIP+CSV/X9.37 formats above, which always carry one. Expected
    (case/whitespace-insensitive) columns: account_number, check_number,
    presented_amount, presented_date, and an optional account_name used only when
    auto_create_accounts creates a new account — same shape as
    networks/ach/bulk_import.py::_submission_from_row for ACH's own tabular path."""
    account_number = require_str(row, "account_number")
    if auto_create_accounts:
        account = account_service.get_or_create_account_by_number(
            session, tenant_id, account_number, default_name=optional_str(row, "account_name"), customer_id=scoped_customer_id
        )
    else:
        account = account_service.get_account_by_number(session, tenant_id, account_number, customer_id=scoped_customer_id)
        if account is None:
            raise RowFieldError(f"No account found with account number {account_number!r}")

    paid_item = ingest_paid_item(
        session,
        tenant_id,
        PaidItemSubmission(
            account_id=account.id,
            check_number=require_str(row, "check_number"),
            presented_amount=parse_decimal(row, "presented_amount"),
            presented_date=parse_date(row, "presented_date"),
            source=PaidItemSource.BULK_FILE,
        ),
        scoped_customer_id=scoped_customer_id,
    )
    return paid_item.id


def ingest_paid_item_tabular_rows(
    session: Session,
    tenant_id: uuid.UUID,
    rows: list[dict[str, Any]],
    *,
    auto_create_accounts: bool = False,
    scoped_customer_id: uuid.UUID | None = None,
) -> list[BulkFileRowResult]:
    """Entry point for the no-image, delimited/Excel check-presentment format. One DB
    transaction per row, same isolation pattern as every other bulk importer in this app."""
    results: list[BulkFileRowResult] = []
    for index, row in enumerate(rows):
        row_label = f"row {index + 2}"  # +2: 1-based, plus the header row itself
        try:
            paid_item_id = _submission_from_tabular_row(
                session, tenant_id, row, auto_create_accounts=auto_create_accounts, scoped_customer_id=scoped_customer_id
            )
            session.commit()
            results.append(BulkFileRowResult(row_label=row_label, success=True, created_id=paid_item_id))
        except Exception as exc:  # noqa: BLE001 — isolate row failures in a bulk file
            session.rollback()
            results.append(BulkFileRowResult(row_label=row_label, success=False, error=str(exc)))
    return results


def ingest_x937_items(
    session: Session,
    tenant_id: uuid.UUID,
    items: list[X937CheckItem],
    *,
    scoped_customer_id: uuid.UUID | None = None,
) -> list[BulkFileRowResult]:
    """Entry point for X9.37 cash letter bulk check-image import."""
    results: list[BulkFileRowResult] = []
    for item in items:
        row_label = f"item {item.item_number} (routing {item.routing_number}, on-us {item.account_number})"
        try:
            if not item.account_number:
                raise RowFieldError("On-Us field is blank -- could not determine an account number for this item")
            if not item.check_number:
                raise RowFieldError("Auxiliary On-Us field is blank -- could not determine a check number for this item")
            paid_item_id = _create_paid_item_and_images(
                session,
                tenant_id,
                account_number=item.account_number,
                check_number=item.check_number,
                amount=item.amount,
                presented_date=item.presented_date,
                front_bytes=item.front_image_bytes,
                back_bytes=item.back_image_bytes,
                image_filename=row_label,
                scoped_customer_id=scoped_customer_id,
            )
            session.commit()
            results.append(BulkFileRowResult(row_label=row_label, success=True, created_id=paid_item_id))
        except (RowFieldError, UnsupportedImageError) as exc:
            session.rollback()
            results.append(BulkFileRowResult(row_label=row_label, success=False, error=str(exc)))
        except Exception as exc:  # noqa: BLE001 — isolate row failures in a bulk file
            session.rollback()
            results.append(BulkFileRowResult(row_label=row_label, success=False, error=str(exc)))
    return results
