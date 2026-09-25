# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

_DATE_FORMATS = ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%Y/%m/%d")


class RowFieldError(ValueError):
    """A specific row/entry's field failed to parse or is missing — the message is
    surfaced directly to the user in the bulk-upload results page, so it's written to be
    read by someone looking at their own spreadsheet row, not a developer."""


def require_str(row: dict[str, Any], key: str) -> str:
    value = row.get(key)
    text = str(value).strip() if value is not None else ""
    if not text or text.lower() == "nan":  # pandas renders empty cells as float NaN
        raise RowFieldError(f"'{key}' is required")
    return text


def optional_str(row: dict[str, Any], key: str) -> str | None:
    value = row.get(key)
    if value is None:
        return None
    text = str(value).strip()
    return text if text and text.lower() != "nan" else None


def parse_decimal(row: dict[str, Any], key: str) -> Decimal:
    raw = require_str(row, key)
    cleaned = raw.replace("$", "").replace(",", "").strip()
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        raise RowFieldError(f"'{key}' is not a valid amount: {raw!r}") from None


def parse_date(row: dict[str, Any], key: str) -> date:
    raw = require_str(row, key)
    # Excel dates read via pandas/openpyxl often arrive as "2026-01-15 00:00:00" (a
    # stringified Timestamp) rather than a plain date — strip a trailing time component
    # before trying the date-only formats below.
    raw = raw.split(" ")[0] if " " in raw else raw
    try:
        return date.fromisoformat(raw)
    except ValueError:
        pass
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    raise RowFieldError(f"'{key}' is not a recognizable date: {raw!r}")
