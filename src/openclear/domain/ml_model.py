# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import enum
import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, Enum, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from openclear.db.base import Base, new_uuid


class MlModelStatus(str, enum.Enum):
    TRAINING = "training"
    ACTIVE = "active"
    RETIRED = "retired"
    FAILED = "failed"


class MlModel(Base):
    """Model registry, one row per trained artifact. Separate models per network_code
    (check vs ach feature spaces barely overlap) — never assume a single global model.

    Three kinds of model, by owner (FIX_PLAN.md Phase 5):
    - shared network model: tenant_id and customer_id both NULL — trained on every
      SHARED bank's decisions, controlled only by the platform operator;
    - bank-only model: tenant_id set, customer_id NULL — one PRIVATE bank's own model;
    - customer model: customer_id set (and tenant_id, its bank).
    ml/predict.py picks among them. A given (network_code, tenant_id, customer_id) slot
    can have at most one ACTIVE row at a time — enforced in ml/registry.py, not a DB
    constraint, since TRAINING/RETIRED/FAILED rows for the same slot coexist as history."""

    __tablename__ = "ml_model"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    network_code: Mapped[str] = mapped_column(ForeignKey("payment_network.code"), nullable=False, index=True)
    customer_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("customer.id"), nullable=True, index=True)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tenant.id"), nullable=True, index=True)

    version: Mapped[str] = mapped_column(String(64), nullable=False)
    algorithm: Mapped[str] = mapped_column(String(64), nullable=False)
    artifact_path: Mapped[str] = mapped_column(String(500), nullable=False)
    # SHA-256 of the artifact file, recorded when it's written. ml/registry.py checks the
    # file against it before unpickling, and refuses a row with none (see ArtifactStore).
    artifact_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)

    trained_from_decision_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    metrics_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    status: Mapped[MlModelStatus] = mapped_column(
        Enum(MlModelStatus, name="ml_model_status", native_enum=False, length=20),
        nullable=False,
        default=MlModelStatus.TRAINING,
    )
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
