# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from openclear.domain.ml_model import MlModelStatus


class MlModelRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    network_code: str
    version: str
    algorithm: str
    trained_from_decision_count: int
    metrics_json: dict | None
    status: MlModelStatus
    activated_at: datetime | None
    created_at: datetime


class RetrainResponse(BaseModel):
    network_code: str
    promoted: bool
    metrics: dict[str, float]
    model: MlModelRead


class SharedModelSummaryRead(BaseModel):
    """What a bank may see about the shared model — counts only, never other banks."""

    network_code: str
    version: str | None
    activated_at: datetime | None
    trained_from_decision_count: int | None
    contributing_bank_count: int


class PendingFraudExampleRead(BaseModel):
    """A fraud-training example from a bank on the shared model, awaiting the platform
    operator's approval before it can train the shared model (api/v1/platform_ml.py)."""

    exception_id: uuid.UUID
    tenant_slug: str
    network_code: str
    outcome: str
    reason_code: str | None
    notes: str | None
    submitted_at: datetime


class SwitchLockClearedRead(BaseModel):
    tenant_id: uuid.UUID
    tenant_slug: str
