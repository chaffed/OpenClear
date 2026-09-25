# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from openclear.auth.deps import require_permission
from openclear.db.session import get_db
from openclear.db.tenancy import TenantContext
from openclear.domain.ml_model import MlModel
from openclear.domain.tenant import MlModelSource, Tenant
from openclear.ml.registry import activate_model
from openclear.ml.train import InsufficientTrainingData, RetrainCooldownActive, train_model
from openclear.networks.registry import registered_codes
from openclear.schemas.ml_model import MlModelRead, RetrainResponse, SharedModelSummaryRead
from openclear.services import audit_log_service, tenant_ml_service

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/payment-networks", response_model=list[str])
def list_payment_networks(ctx: TenantContext = Depends(require_permission("admin:manage"))) -> list[str]:
    return registered_codes()


def _require_bank_only_model(db: Session, ctx: TenantContext) -> None:
    if db.get(Tenant, ctx.tenant_id).ml_model_source != MlModelSource.PRIVATE:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Your organization uses the shared model, which only the platform operator retrains or activates.",
        )


@router.post("/ml/retrain", response_model=RetrainResponse)
def retrain(
    network_code: str,
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_permission("admin:manage")),
) -> RetrainResponse:
    """Retrains this organization's bank-only model (403 for a bank on the shared model)."""
    _require_bank_only_model(db, ctx)
    try:
        result = train_model(db, network_code, tenant_id=ctx.tenant_id)
    except (InsufficientTrainingData, RetrainCooldownActive) as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None
    audit_log_service.record_action(
        db, ctx.tenant_id, actor_user_id=ctx.user_id, channel="api", action="ml_model.retrain",
        summary=f"Retrained the bank-only {network_code} model ({'activated' if result.promoted else 'not activated'})",
        resource_type="ml_model", resource_id=result.model_row.id,
    )
    db.commit()

    return RetrainResponse(
        network_code=network_code,
        promoted=result.promoted,
        metrics=result.metrics,
        model=MlModelRead.model_validate(result.model_row),
    )


@router.get("/ml/models", response_model=list[MlModelRead])
def list_ml_models(
    network_code: str | None = None,
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_permission("admin:manage")),
) -> list[MlModelRead]:
    """This organization's own models: its bank-only models and its customers' models.
    (It used to list every organization's models.) For the shared model, see
    /admin/ml/shared-model."""
    stmt = select(MlModel).where(MlModel.tenant_id == ctx.tenant_id).order_by(MlModel.created_at.desc())
    if network_code is not None:
        stmt = stmt.where(MlModel.network_code == network_code)
    rows = db.execute(stmt).scalars().all()
    return [MlModelRead.model_validate(r) for r in rows]


@router.get("/ml/shared-model", response_model=list[SharedModelSummaryRead])
def shared_model_summary(
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_permission("admin:manage")),
) -> list[SharedModelSummaryRead]:
    """The shared model's active version per network, with counts only (never which other
    banks contribute)."""
    return [SharedModelSummaryRead.model_validate(s, from_attributes=True) for s in tenant_ml_service.shared_model_summaries(db)]


@router.patch("/ml/models/{model_id}/activate", response_model=MlModelRead)
def activate_ml_model(
    model_id: uuid.UUID,
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_permission("admin:manage")),
) -> MlModelRead:
    """Activates one of this organization's bank-only models (404 for any other model)."""
    try:
        model = activate_model(db, model_id, expected_customer_id=None, expected_tenant_id=ctx.tenant_id)
    except ValueError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Model not found") from None
    audit_log_service.record_action(
        db, ctx.tenant_id, actor_user_id=ctx.user_id, channel="api", action="ml_model.activate",
        summary=f"Activated bank-only {model.network_code} model {model.version}", resource_type="ml_model", resource_id=model.id,
    )
    db.commit()
    return MlModelRead.model_validate(model)
