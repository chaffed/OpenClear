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
    # A check returned with one of the bank's check return reasons (GET /return-reasons/check):
    # its text and code are recorded instead of reason_code.
    check_return_reason_id: uuid.UUID | None = None


class DecideRequest(BaseModel):
    outcome: DecisionOutcome
    reason_code: str = ""
    notes: str | None = None
    ach_return_reason_id: uuid.UUID | None = None
    # A check returned with one of the bank's check return reasons (GET /return-reasons/check):
    # its text and code are recorded instead of reason_code.
    check_return_reason_id: uuid.UUID | None = None


class DecisionRevisionRead(BaseModel):
    """An earlier version of a decision, changed or taken back before the cut-off."""

    model_config = ConfigDict(from_attributes=True)

    outcome: DecisionOutcome
    reason_code: str
    return_transaction_code: str | None
    notes: str | None
    decided_by_user_id: uuid.UUID | None
    decided_at: datetime
    revised_by_user_id: uuid.UUID | None
    revised_at: datetime
    # Taken back (the item was open again), rather than changed to something else.
    undone: bool


class DecisionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    exception_item_id: uuid.UUID
    outcome: DecisionOutcome
    reason_code: str
    return_transaction_code: str | None
    notes: str | None
    submitted_by_user_id: uuid.UUID | None
    # None for a decision made automatically at the cut-off (the customer's default).
    decided_by_user_id: uuid.UUID | None
    decided_at: datetime
    # When it became final and went to the bank's core (the decision feed, webhooks, decision
    # files). Until then it can be changed: PUT or DELETE …/decision, up to `changeable_until`,
    # the item's cut-off (UI_PLAN.md UI-D13).
    locked_at: datetime | None = None
    changeable_until: datetime | None = None
    # Under dual control: a change recommended for someone else to approve, and still waiting.
    change_pending: bool = False
