# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from openclear.domain.ach_transaction import AchTransactionType


class CheckFraudExampleCreate(BaseModel):
    # Attach mode: set paid_item_id, leave the rest below unset. Raw-entry mode: leave
    # paid_item_id unset and provide the transaction fields instead.
    paid_item_id: uuid.UUID | None = None
    account_id: uuid.UUID | None = None
    check_number: str | None = None
    presented_amount: Decimal | None = None
    presented_date: date | None = None
    reason_code: str
    notes: str | None = None


class AchFraudExampleCreate(BaseModel):
    ach_transaction_id: uuid.UUID | None = None
    account_id: uuid.UUID | None = None
    originator_id: str | None = None
    originator_name: str | None = None
    receiver_id: str | None = None
    amount: Decimal | None = None
    transaction_type: AchTransactionType | None = None
    sec_code: str | None = None
    trace_number: str | None = None
    effective_date: date | None = None
    ach_return_reason_id: uuid.UUID
    notes: str | None = None


class FraudExampleRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    network_code: str
    is_correction: bool
    retracted_at: datetime | None
    created_at: datetime
