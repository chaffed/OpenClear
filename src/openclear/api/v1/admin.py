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
from openclear.domain.tenant import Tenant
from openclear.ml.registry import activate_model
from openclear.ml.train import InsufficientTrainingData, RetrainCooldownActive, train_model
from openclear.networks.registry import registered_codes
from openclear.schemas.ml_model import MlModelRead, RetrainResponse
from openclear.services import audit_log_service

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/payment-networks", response_model=list[str])
def list_payment_networks(ctx: TenantContext = Depends(require_permission("admin:manage"))) -> list[str]:
    return registered_codes()


def _require_scoring_on(db: Session, ctx: TenantContext) -> None:
    if not db.get(Tenant, ctx.tenant_id).ml_scoring_enabled:
        raise HTTPException(status.HTTP_409_CONFLICT, "Fraud scoring is off for your organization. Turn it on first.")


@router.post("/ml/retrain", response_model=RetrainResponse)
def retrain(
    network_code: str,
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_permission("admin:manage")),
) -> RetrainResponse:
    """Retrains this organization's own model from its own final decisions (409 while fraud
    scoring is off). The score is advisory: it never decides an item."""
    _require_scoring_on(db, ctx)
    try:
        result = train_model(db, network_code, tenant_id=ctx.tenant_id)
    except (InsufficientTrainingData, RetrainCooldownActive) as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None
    audit_log_service.record_action(
        db, ctx.tenant_id, actor_user_id=ctx.user_id, channel="api", action="ml_model.retrain",
        summary=f"Retrained the bank's {network_code} model ({'activated' if result.promoted else 'not activated'})",
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
    """This organization's own models."""
    stmt = select(MlModel).where(MlModel.tenant_id == ctx.tenant_id).order_by(MlModel.created_at.desc())
    if network_code is not None:
        stmt = stmt.where(MlModel.network_code == network_code)
    rows = db.execute(stmt).scalars().all()
    return [MlModelRead.model_validate(r) for r in rows]


@router.patch("/ml/models/{model_id}/activate", response_model=MlModelRead, responses={202: {
    "description": "Segregation of duties is enforced: the activation waits for a second admin (Settings → Changes)",
    "content": {"application/json": {"example": {"status": "pending", "change_request_id": "…", "detail": "…"}}}}})
def activate_ml_model(
    model_id: uuid.UUID,
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_permission("admin:manage")),
):
    """Activates one of this organization's own models (404 for any other model). When the
    bank's sensitive settings need a second admin, it answers `202` with `{"status": "pending",
    "change_request_id"}` instead: a second admin approves it (Settings → Changes, or
    `POST /api/v1/changes/{id}/approve`)."""
    from fastapi.responses import JSONResponse

    from openclear.services import change_request_service

    if change_request_service.needs_approval(db, ctx.tenant_id, "ml_model.activate"):
        try:
            outcome = change_request_service.request_model_activation(db, ctx.tenant_id, model_id, user_id=ctx.user_id, channel="api")
        except change_request_service.ChangeRequestError as exc:
            if "waiting" in str(exc):
                raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Model not found") from None
        db.commit()
        return JSONResponse({"status": "pending", "change_request_id": str(outcome.change.id),
                             "detail": "A second admin must approve this (Settings → Changes, or POST /api/v1/changes/{id}/approve)."},
                            status_code=status.HTTP_202_ACCEPTED)
    try:
        model = activate_model(db, model_id, expected_tenant_id=ctx.tenant_id)
    except ValueError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Model not found") from None
    audit_log_service.record_action(
        db, ctx.tenant_id, actor_user_id=ctx.user_id, channel="api", action="ml_model.activate",
        summary=f"Activated the bank's {model.network_code} model {model.version}", resource_type="ml_model", resource_id=model.id,
    )
    db.commit()
    return MlModelRead.model_validate(model)
