# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import enum
import uuid
from dataclasses import dataclass, field
from decimal import Decimal


class AchExceptionType(str, enum.Enum):
    UNAUTHORIZED_ORIGINATOR = "unauthorized_originator"
    RECEIVER_ID_NOT_PERMITTED = "receiver_id_not_permitted"
    AMOUNT_EXCEEDS_LIMIT = "amount_exceeds_limit"
    FREQUENCY_EXCEEDED = "frequency_exceeded"
    SEC_CODE_NOT_PERMITTED = "sec_code_not_permitted"


@dataclass(frozen=True, slots=True)
class AuthorizationSnapshot:
    id: uuid.UUID
    receiver_id: str | None  # None = wildcard, matches any receiver for this originator
    max_amount: Decimal | None
    frequency_limit: int | None
    allowed_sec_codes: list[str] | None


@dataclass(frozen=True, slots=True)
class AchMatchInputs:
    is_debit: bool
    debit_block_all: bool
    amount: Decimal
    sec_code: str
    receiver_id: str | None
    # Every active, in-window authorization for this (account, originator_id) — matching
    # picks the most specific one (exact receiver_id beats a wildcard rule); see rules.py.
    candidate_authorizations: list[AuthorizationSnapshot] = field(default_factory=list)
    debit_count_this_period: int = 0
