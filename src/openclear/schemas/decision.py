# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from openclear.domain.decision import DecisionOutcome


class RecommendRequest(BaseModel):
    outcome: DecisionOutcome
    # Optional at this layer: required only for an ACH exception recommended as RETURN,
    # enforced in services/decision_service.py — every other network/outcome combination
    # still needs reason_code below instead, exactly as before this field existed.
    reason_code: str = ""
    notes: str | None = None
    ach_return_reason_id: uuid.UUID | None = None


class DecideRequest(BaseModel):
    outcome: DecisionOutcome
    reason_code: str = ""
    notes: str | None = None
    ach_return_reason_id: uuid.UUID | None = None


class DecisionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    exception_item_id: uuid.UUID
    outcome: DecisionOutcome
    reason_code: str
    return_transaction_code: str | None
    notes: str | None
    submitted_by_user_id: uuid.UUID | None
    decided_by_user_id: uuid.UUID
    decided_at: datetime
