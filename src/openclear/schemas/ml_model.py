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
