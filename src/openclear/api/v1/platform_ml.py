# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""The platform operator's controls for the shared fraud-scoring model (FIX_PLAN.md
Phase 5). The shared model scores every bank on it, so no single bank's admins may
retrain, activate, or inspect it — only a platform API key with the "shared_model" scope
(scripts/create_metering_api_key.py --scope shared_model), sent as X-Api-Key.

Also here: approving banks' fraud-training examples for use in the shared model (an
unreviewed example could skew scoring for every bank), and lifting a new bank's 90-day
lock on switching to a bank-only model. Actions that concern one bank are written to that
bank's own audit log, with no user as the actor."""

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from openclear.auth.platform_api_key_deps import require_platform_scope
from openclear.db.session import get_db
from openclear.domain.decision import Decision
from openclear.domain.exception_item import ExceptionItem, ExceptionItemSource
from openclear.domain.ml_model import MlModel
from openclear.domain.platform_api_key import PlatformApiKey
from openclear.domain.tenant import MlModelSource, Tenant
from openclear.ml.registry import ArtifactIntegrityError, ArtifactStore, activate_model, list_slot_models
from openclear.ml.train import InsufficientTrainingData, RetrainCooldownActive, train_model
from openclear.networks.registry import registered_codes
from openclear.schemas.ml_model import MlModelRead, PendingFraudExampleRead, RetrainResponse, SwitchLockClearedRead
from openclear.services import audit_log_service, tenant_ml_service

router = APIRouter(prefix="/platform", tags=["platform-ml"])
_operator = require_platform_scope("shared_model")


def _shared_model_or_404(db: Session, model_id: uuid.UUID) -> MlModel:
    model = db.get(MlModel, model_id)
    if model is None or model.tenant_id is not None or model.customer_id is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Shared model not found")
    return model


@router.get("/ml/models", response_model=list[MlModelRead])
def list_shared_models(
    network_code: str | None = None, db: Session = Depends(get_db), _key: PlatformApiKey = Depends(_operator)
) -> list[MlModelRead]:
    networks = [network_code] if network_code else registered_codes()
    return [MlModelRead.model_validate(m) for code in networks for m in list_slot_models(db, code)]


@router.post("/ml/retrain", response_model=RetrainResponse)
def retrain_shared_model(
    network_code: str, db: Session = Depends(get_db), key: PlatformApiKey = Depends(_operator)
) -> RetrainResponse:
    """Trains a challenger for the shared model, promoted only by the champion/challenger rule;
    activating one by hand waits for an operator (`PATCH .../activate`). In the provider's log."""
    from openclear.services import platform_log_service

    try:
        result = train_model(db, network_code)
    except (InsufficientTrainingData, RetrainCooldownActive) as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None
    platform_log_service.record(db, actor_key_id=key.id, action="platform.shared_model.retrain", resource_type="ml_model",
                                resource_id=result.model_row.id,
                                summary=f"Retrained the shared {network_code} model: {result.model_row.version}"
                                        + (", promoted by the champion/challenger rule" if result.promoted else ", not promoted"))
    db.commit()
    return RetrainResponse(
        network_code=network_code, promoted=result.promoted, metrics=result.metrics,
        model=MlModelRead.model_validate(result.model_row),
    )


@router.patch("/ml/models/{model_id}/activate", response_model=MlModelRead,
              responses={202: {"description": "Waiting for two of the platform's staff (DUAL_CONTROL_PLAN.md DC4)"}})
def activate_shared_model(
    model_id: uuid.UUID, db: Session = Depends(get_db), key: PlatformApiKey = Depends(_operator)
):
    """Asks for the shared model to be activated: it scores every bank that uses it, so it waits
    for a person among the platform's operators to approve it in the operator view (`202` with
    `change_request_id`). Already active: `200` with the model."""
    from fastapi.responses import JSONResponse

    from openclear.services import change_request_service

    model = _shared_model_or_404(db, model_id)
    try:
        outcome = change_request_service.request_or_apply(db, "platform.shared_model.activate", {"model_id": str(model_id)}, tenant_id=None,
                                                          user_id=None, channel="api", requested_with=str(key.id),
                                                          summary=f"Activate the shared {model.network_code} model {model.version}")
    except change_request_service.ChangeRequestError as exc:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None
    db.commit()
    if outcome.waiting:
        return JSONResponse({"status": "pending", "change_request_id": str(outcome.change.id),
                             "detail": "A platform operator must approve this in the operator view (Changes)."}, status_code=202)
    return MlModelRead.model_validate(db.get(MlModel, model_id))


@router.get("/ml/models/{model_id}/feature-importance", response_model=dict[str, float])
def shared_model_feature_importance(
    model_id: uuid.UUID, db: Session = Depends(get_db), _key: PlatformApiKey = Depends(_operator)
) -> dict[str, float]:
    """Model coefficients by feature, largest effect first. Operator-only: banks never
    see the shared model's internals."""
    model = _shared_model_or_404(db, model_id)
    if not model.artifact_path:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "This model has no trained artifact")
    try:
        return ArtifactStore().load_model(model).feature_importance()
    except ArtifactIntegrityError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc


@router.get("/ml/fraud-examples/pending", response_model=list[PendingFraudExampleRead])
def pending_fraud_examples(
    db: Session = Depends(get_db), _key: PlatformApiKey = Depends(_operator)
) -> list[PendingFraudExampleRead]:
    """Fraud-training examples from banks on the shared model that aren't yet approved
    for training it (oldest first). Bank-only banks' examples never need approval: they
    only ever train that bank's own model."""
    rows = db.execute(
        select(ExceptionItem, Decision, Tenant.slug)
        .join(Decision, Decision.exception_item_id == ExceptionItem.id)
        .join(Tenant, Tenant.id == ExceptionItem.tenant_id)
        .where(
            ExceptionItem.source == ExceptionItemSource.TRAINING_BACKFILL,
            ExceptionItem.retracted_at.is_(None),
            ExceptionItem.shared_training_approved_at.is_(None),
            Tenant.ml_model_source == MlModelSource.SHARED,
        )
        .order_by(ExceptionItem.created_at)
    ).all()
    return [
        PendingFraudExampleRead(
            exception_id=item.id, tenant_slug=slug, network_code=item.network_code,
            outcome=decision.outcome.value, reason_code=decision.reason_code, notes=decision.notes,
            submitted_at=item.created_at,
        )
        for item, decision, slug in rows
    ]


@router.post("/ml/fraud-examples/{exception_id}/approve", status_code=status.HTTP_204_NO_CONTENT)
def approve_fraud_example(
    exception_id: uuid.UUID, db: Session = Depends(get_db), key: PlatformApiKey = Depends(_operator)
) -> None:
    item = db.get(ExceptionItem, exception_id)
    if item is None or item.source != ExceptionItemSource.TRAINING_BACKFILL or item.retracted_at is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Fraud-training example not found")
    item.shared_training_approved_at = datetime.now(timezone.utc)
    audit_log_service.record_action(
        db, item.tenant_id, actor_user_id=None, channel="api", action="fraud_example.approve_for_shared_model",
        summary=f"Platform operator ({key.name}) approved a fraud-training example for the shared model",
        resource_type="exception_item", resource_id=item.id,
    )
    db.commit()


@router.post("/tenants/{tenant_id}/ml-switch-lock/clear", response_model=SwitchLockClearedRead)
def clear_bank_switch_lock(
    tenant_id: uuid.UUID, db: Session = Depends(get_db), key: PlatformApiKey = Depends(_operator)
) -> SwitchLockClearedRead:
    """Lets a new bank switch to a bank-only model before its 90 days are up."""
    tenant = tenant_ml_service.clear_switch_lock(db, tenant_id)
    if tenant is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Organization not found")
    audit_log_service.record_action(
        db, tenant.id, actor_user_id=None, channel="api", action="tenant.ml_switch_lock_cleared",
        summary=f"Platform operator ({key.name}) lifted the new-organization lock on switching to a bank-only model",
        resource_type="tenant", resource_id=tenant.id,
    )
    db.commit()
    return SwitchLockClearedRead(tenant_id=tenant.id, tenant_slug=tenant.slug)
