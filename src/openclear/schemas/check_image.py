# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from openclear.domain.check_image import OcrStatus


class CheckImageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    paid_item_id: uuid.UUID | None
    ocr_status: OcrStatus
    ocr_extracted_amount: Decimal | None
    ocr_extracted_payee: str | None
    ocr_confidence: float | None
    ocr_provider: str | None
    created_at: datetime
    processed_at: datetime | None
