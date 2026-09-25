# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import enum
import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal


class CheckExceptionType(str, enum.Enum):
    DUPLICATE_PAID = "duplicate_paid"
    STOPPED = "stopped"
    VOIDED = "voided"
    NOT_IN_FILE = "not_in_file"
    AMOUNT_MISMATCH = "amount_mismatch"
    PAYEE_MISMATCH = "payee_mismatch"
    STALE_DATED = "stale_dated"


@dataclass(frozen=True, slots=True)
class IssuedItemSnapshot:
    """The subset of an issued_item's state relevant to matching, decoupled from the ORM
    row so rules.py stays a pure function with no DB session dependency."""

    id: uuid.UUID
    amount: Decimal
    payee_name: str
    issue_date: date
    status: str


@dataclass(frozen=True, slots=True)
class CheckMatchInputs:
    presented_amount: Decimal
    presented_date: date
    is_duplicate_paid: bool
    active_stop_found: bool
    candidate_issued_item: IssuedItemSnapshot | None
    stale_date_threshold_days: int
    payee_fuzzy_threshold: float
    ocr_payee: str | None = None
    ocr_confidence: float | None = None
