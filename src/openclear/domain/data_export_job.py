# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from openclear.db.base import Base, new_uuid


class DataExportJobStatus(str, enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class DataExportJob(Base):
    """Tracks one "export everything" request — the archive itself is generated in the
    background (services/data_export_service.py::run_export_job), same "represent a unit
    of background work as a row" shape already used by BulkUploadFile/MlModel, so the UI
    has a job list with status instead of a fire-and-forget action with no visibility.

    customer_id is None for a whole-tenant export, or set to scope it to one customer's
    own data — same shape as MlModel.customer_id / SsoConnection.customer_id. Gated on
    the data_export:run permission, which is deliberately NOT part of the default Admin
    grant (see auth/permissions.py) — a bulk data export is a different class of risk
    than every other permission in this catalog."""

    __tablename__ = "data_export_job"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id"), nullable=False, index=True)
    customer_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("customer.id"), nullable=True, index=True)
    requested_by_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("user.id"), nullable=False)

    status: Mapped[DataExportJobStatus] = mapped_column(
        Enum(DataExportJobStatus, name="data_export_job_status", native_enum=False, length=20),
        nullable=False,
        default=DataExportJobStatus.PENDING,
    )
    archive_path: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(1000), nullable=True)

    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
