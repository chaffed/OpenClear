# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from openclear.domain.ach_authorization_rule import AchAuthorizationStatus
from openclear.domain.ach_transaction import AchMatchStatus, AchSettlementStatus, AchTransactionType


class AchAuthorizationCreate(BaseModel):
    account_id: uuid.UUID
    originator_id: str
    originator_name: str
    receiver_id: str | None = None  # None = blanket authorization, any receiver for this originator
    max_amount: Decimal | None = None
    frequency_limit: int | None = None
    allowed_sec_codes: list[str] | None = None
    effective_date: date
    expiration_date: date | None = None


class AchAuthorizationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    account_id: uuid.UUID
    originator_id: str
    originator_name: str
    receiver_id: str | None
    status: AchAuthorizationStatus
    max_amount: Decimal | None
    frequency_limit: int | None
    allowed_sec_codes: list[str] | None
    effective_date: date
    expiration_date: date | None
    created_at: datetime
    # The customer the record belongs to (none for the bank's own accounts), and when it last
    # changed (the list endpoints' updated_since filters on it).
    customer_id: uuid.UUID | None = None
    updated_at: datetime | None = None


class AchTransactionCreate(BaseModel):
    account_id: uuid.UUID
    originator_id: str
    originator_name: str
    receiver_id: str | None = None
    amount: Decimal
    transaction_type: AchTransactionType
    sec_code: str
    trace_number: str
    effective_date: date


class AchTransactionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    account_id: uuid.UUID
    originator_id: str
    originator_name: str
    receiver_id: str | None
    amount: Decimal
    transaction_type: AchTransactionType
    sec_code: str
    trace_number: str
    effective_date: date
    match_status: AchMatchStatus
    settlement_status: AchSettlementStatus
    created_at: datetime
    # The customer the record belongs to (none for the bank's own accounts), and when it last
    # changed (the list endpoints' updated_since filters on it).
    customer_id: uuid.UUID | None = None
    updated_at: datetime | None = None
