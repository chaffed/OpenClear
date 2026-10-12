# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from openclear.domain.exception_item import ExceptionStatus


class ExceptionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    network_code: str
    source_item_id: uuid.UUID
    related_reference_id: uuid.UUID | None
    exception_types: list[str]
    status: ExceptionStatus
    ml_score: float | None
    ml_model_version: str | None
    decision_deadline: datetime | None
    created_at: datetime
    # The customer the record belongs to (none for the bank's own accounts), and when it last
    # changed (the list endpoints' updated_since filters on it).
    customer_id: uuid.UUID | None = None
    updated_at: datetime | None = None

    @classmethod
    def from_orm_row(cls, row) -> "ExceptionRead":
        data = {c.name: getattr(row, c.name) for c in row.__table__.columns}
        data["exception_types"] = row.exception_types.split(",") if row.exception_types else []
        return cls.model_validate(data)
