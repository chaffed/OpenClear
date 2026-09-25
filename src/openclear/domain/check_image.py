# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import enum
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import JSON, DateTime, Enum, Float, ForeignKey, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column

from openclear.db.base import Base, new_uuid


class OcrStatus(str, enum.Enum):
    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"


class CheckImage(Base):
    __tablename__ = "check_image"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id"), nullable=False, index=True)
    paid_item_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("paid_item.id"), nullable=True, index=True)
    # NULL = bank-wide (no customer scope known/assigned yet); a real value scopes this
    # image to that one customer, same nullable-FK pattern as Account/IssuedItem/etc.
    # (repositories/base.py::CustomerScopedRepository). Denormalized from the resolved
    # Account/PaidItem at creation time, not blindly the uploader's own scope — see
    # networks/check/bulk_import.py and web/routers/check_images.py::upload_check_image.
    customer_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("customer.id"), nullable=True, index=True)

    front_image_path: Mapped[str] = mapped_column(String(1000), nullable=False)
    back_image_path: Mapped[str | None] = mapped_column(String(1000), nullable=True)

    ocr_status: Mapped[OcrStatus] = mapped_column(
        Enum(OcrStatus, name="ocr_status", native_enum=False, length=20), nullable=False, default=OcrStatus.PENDING
    )
    ocr_raw_result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    ocr_extracted_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    ocr_extracted_payee: Mapped[str | None] = mapped_column(String(255), nullable=True)
    ocr_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    ocr_provider: Mapped[str | None] = mapped_column(String(32), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
