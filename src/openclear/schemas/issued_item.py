# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from openclear.domain.issued_item import IssuedItemStatus


class IssuedItemCreate(BaseModel):
    account_id: uuid.UUID
    check_number: str
    amount: Decimal
    payee_name: str
    issue_date: date


class IssuedItemRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    account_id: uuid.UUID
    check_number: str
    amount: Decimal
    payee_name: str
    issue_date: date
    status: IssuedItemStatus
    void_reason: str | None
    created_at: datetime
    # The customer the record belongs to (none for the bank's own accounts), and when it last
    # changed (the list endpoints' updated_since filters on it).
    customer_id: uuid.UUID | None = None
    updated_at: datetime | None = None


class IssuedItemVoidRequest(BaseModel):
    reason: str
