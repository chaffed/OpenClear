# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column

from openclear.db.base import Base, new_uuid


class BulkUploadCreatedRecord(Base):
    """One row per record a bulk upload successfully created — the durable link from a
    `BulkUploadFile` to what it produced, which services/bulk_upload_reversal_service.py
    needs to back an upload out later. `resource_type`/`resource_id` is a generic
    pointer (no DB-level FK, since the type varies by upload kind), the same pattern
    `ExceptionItem.source_item_id` already uses for the same reason. A check-image bulk
    row tracks two records (`paid_item` and `check_image`) sharing the same `row_label`.

    `reversed_at`/`reversed_by_user_id` are set when this specific record was
    successfully backed out — nullable, since some kinds' rows are always "backed out"
    the moment the whole upload is (see back_out_upload's record-only accounts/ACH
    path), while others (issued_item/tenant_membership/paid_item) can individually fail
    to back out (already voided, already paid, etc.) and stay unreversed even after the
    upload-level backout attempt completes."""

    __tablename__ = "bulk_upload_created_record"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id"), nullable=False, index=True)
    bulk_upload_file_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bulk_upload_file.id"), nullable=False, index=True)

    resource_type: Mapped[str] = mapped_column(String(50), nullable=False)
    resource_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    row_label: Mapped[str] = mapped_column(String(100), nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    reversed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reversed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("user.id"), nullable=True)
