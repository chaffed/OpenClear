# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from openclear.domain.paid_item import PaidItemMatchStatus, PaidItemSettlementStatus, PaidItemSource


class PaidItemCreate(BaseModel):
    account_id: uuid.UUID
    check_number: str
    presented_amount: Decimal
    presented_date: date


class PaidItemRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    account_id: uuid.UUID
    check_number: str
    presented_amount: Decimal
    presented_date: date
    # Where it was presented: api, bulk_file, manual, or teller (cashed at a branch, TELLER_PLAN.md TL-D3).
    source: PaidItemSource
    matched_issued_item_id: uuid.UUID | None
    match_status: PaidItemMatchStatus
    settlement_status: PaidItemSettlementStatus
    created_at: datetime
    # The customer the record belongs to (none for the bank's own accounts), and when it last
    # changed (the list endpoints' updated_since filters on it).
    customer_id: uuid.UUID | None = None
    updated_at: datetime | None = None
