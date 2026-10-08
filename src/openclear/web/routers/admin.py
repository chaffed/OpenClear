# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import uuid
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from openclear.config import get_settings
from openclear.db.session import get_db
from openclear.db.tenancy import TenantContext
from openclear.domain.customer_disposition_setting import DispositionMode
from openclear.domain.customer_ml_setting import MlScoringMode
from openclear.domain.decision import Decision
from openclear.domain.exception_item import ExceptionItem, ExceptionItemSource
from openclear.domain.ml_model import MlModel
from openclear.domain.tenant import MlModelSource, Tenant
from openclear.ml.registry import activate_model
from openclear.ml.train import InsufficientTrainingData, RetrainCooldownActive, train_model
from openclear.networks.registry import registered_codes
from openclear.services import (
    ach_return_reason_service,
    audit_log_service,
    auto_disposition_service,
    customer_ml_service,
    customer_service,
    demo_tenant_service,
    sso_service,
    tenant_ml_service,
)
from openclear.web.deps import WebNotFound, render_template, require_any_web_permission, require_web_permission
from openclear.web.security import clear_auth_cookies, verify_csrf

router = APIRouter(prefix="/ui/admin", tags=["web-admin"])


@router.get("")
def admin_home(
    request: Request, db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_any_web_permission("admin:manage", "tenant:manage")),
) -> HTMLResponse:
    # Shared landing page for two otherwise-unrelated audiences (see
    # require_any_web_permission) — each section below is independently gated in the
    # template too, so a viewer only ever sees what their own permission covers.
    models = []
    backfill_counts: dict[str, dict[str, int]] = {}
    is_demo_tenant = False
    tenant = db.get(Tenant, ctx.tenant_id)
    uses_bank_model = tenant.ml_model_source == MlModelSource.PRIVATE
    shared_summaries = []
    if "admin:manage" in ctx.permissions:
        if uses_bank_model:
            # This bank's own bank-only models (every network); each customer's own models
            # have their own dedicated page (the customer ML routes below). The shared
            # model is run by the platform operator and isn't listed here at all.
            models = db.execute(
                select(MlModel)
                .where(MlModel.tenant_id == ctx.tenant_id, MlModel.customer_id.is_(None))
                .order_by(MlModel.network_code, MlModel.created_at.desc())
            ).scalars().all()
        else:
            shared_summaries = tenant_ml_service.shared_model_summaries(db)

        # A live count of THIS bank's labeled decisions per network (its own contribution
        # to whichever model it uses), separate from trained_from_decision_count on each
        # MlModel row, which is frozen as of that model's training run. Same eligibility
        # filter as ml/train.py::_labeled_decisions (features present, not retracted).
        for network_code in registered_codes():
            base_stmt = (
                select(ExceptionItem.source, func.count(Decision.id))
                .join(ExceptionItem, Decision.exception_item_id == ExceptionItem.id)
                .where(
                    ExceptionItem.tenant_id == ctx.tenant_id,
                    ExceptionItem.network_code == network_code,
                    Decision.features_json.is_not(None),
                    ExceptionItem.retracted_at.is_(None),
                )
                .group_by(ExceptionItem.source)
            )
            counts = dict(db.execute(base_stmt).all())
            backfill_counts[network_code] = {
                "live": counts.get(ExceptionItemSource.LIVE, 0),
                "training_backfill": counts.get(ExceptionItemSource.TRAINING_BACKFILL, 0),
            }

        demo_tenant = demo_tenant_service.get_demo_tenant(db)
        is_demo_tenant = demo_tenant is not None and demo_tenant.id == ctx.tenant_id

    sso_connection_count = None
    if "tenant:manage" in ctx.permissions:
        sso_connection_count = len(sso_service.list_connections(db, ctx.tenant_id, customer_id=None))

    return render_template(
        request, "admin/ml_models.html", ctx=ctx, models=models, networks=registered_codes(),
        backfill_counts=backfill_counts, is_demo_tenant=is_demo_tenant, sso_connection_count=sso_connection_count,
        uses_bank_model=uses_bank_model, shared_summaries=shared_summaries,
        min_bank_decisions=get_settings().ml_bank_model_min_decisions,
    )


@router.post("/demo/reset")
def reset_demo(
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("admin:manage")),
    _csrf: None = Depends(verify_csrf),
) -> RedirectResponse:
    # Defense in depth beyond the permission gate: this must never be reachable against
    # any tenant other than the one actually flagged is_demo, even by a crafted request —
    # reset_demo_tenant() itself also only ever looks up the demo tenant fresh, never
    # accepting a caller-supplied id, but this 404s before it even gets that far.
    demo_tenant = demo_tenant_service.get_demo_tenant(db)
    if demo_tenant is None or demo_tenant.id != ctx.tenant_id:
        raise WebNotFound()

    demo_tenant_service.reset_demo_tenant(db)
    db.commit()

    # The current session's user/membership rows were just recreated with new ids —
    # rather than try to keep this session alive against data that changed out from
    # under it, send them back to the login page to sign in again with the same demo
    # credentials.
    response = RedirectResponse(
        "/ui/login?flash=" + quote("Demo data reset — log back in with the demo credentials."), status_code=303
    )
    clear_auth_cookies(response)
    return response


@router.post("/ml/retrain")
def retrain(
    network_code: str = Form(...),
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("admin:manage")),
    _csrf: None = Depends(verify_csrf),
) -> RedirectResponse:
    # Only a bank's own bank-only model can be retrained here; the shared model is run by
    # the platform operator (api/v1/platform_ml.py), never by one bank's admins.
    if db.get(Tenant, ctx.tenant_id).ml_model_source != MlModelSource.PRIVATE:
        return RedirectResponse(
            "/ui/admin?error=" + quote("Your organization uses the shared model, which the platform operator retrains."),
            status_code=303,
        )
    try:
        result = train_model(db, network_code, tenant_id=ctx.tenant_id)
    except (InsufficientTrainingData, RetrainCooldownActive) as exc:
        return RedirectResponse(f"/ui/admin?error={quote(str(exc))}", status_code=303)
    audit_log_service.record_action(
        db, ctx.tenant_id, actor_user_id=ctx.user_id, channel="web", action="ml_model.retrain",
        summary=f"Retrained the bank-only {network_code} model ({'activated' if result.promoted else 'not activated'})",
        resource_type="ml_model", resource_id=result.model_row.id,
    )
    db.commit()
    reason = (result.model_row.metrics_json or {}).get("evaluation", {}).get("reason", "")
    return RedirectResponse(f"/ui/admin?flash={quote(f'Retrained {network_code}. {reason}')}", status_code=303)


@router.post("/ml/models/{model_id}/activate")
def activate(
    model_id: uuid.UUID,
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("admin:manage")),
    _csrf: None = Depends(verify_csrf),
) -> RedirectResponse:
    try:
        # Confined to this bank's own bank-only models (ml/registry.py::activate_model).
        model = activate_model(db, model_id, expected_customer_id=None, expected_tenant_id=ctx.tenant_id)
    except ValueError:
        db.rollback()
        return RedirectResponse("/ui/admin?error=" + quote("That model couldn't be found."), status_code=303)
    audit_log_service.record_action(
        db, ctx.tenant_id, actor_user_id=ctx.user_id, channel="web", action="ml_model.activate",
        summary=f"Activated bank-only {model.network_code} model {model.version}", resource_type="ml_model", resource_id=model.id,
    )
    db.commit()
    return RedirectResponse("/ui/admin?flash=Model+activated.", status_code=303)


def _get_customer_or_404(db: Session, tenant_id: uuid.UUID, customer_id: uuid.UUID):
    customer = customer_service.get_customer(db, tenant_id, customer_id)
    if customer is None:
        raise WebNotFound()
    return customer


@router.get("/customers/{customer_id}/ml")
def customer_ml_detail(
    request: Request,
    customer_id: uuid.UUID,
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("admin:manage")),
) -> HTMLResponse:
    customer = _get_customer_or_404(db, ctx.tenant_id, customer_id)
    summary = customer_ml_service.get_ml_summary(db, ctx.tenant_id, customer_id)
    models = customer_ml_service.list_customer_models(db, customer_id)
    disposition_summary = auto_disposition_service.get_disposition_summary(db, ctx.tenant_id, customer_id)
    ach_return_reasons = ach_return_reason_service.list_ach_return_reasons(db, ctx.tenant_id, active_only=True)
    return render_template(
        request,
        "admin/customer_ml.html",
        ctx=ctx,
        customer=customer,
        summary=summary,
        models=models,
        modes=list(MlScoringMode),
        disposition_summary=disposition_summary,
        disposition_modes=list(DispositionMode),
        ach_return_reasons=ach_return_reasons,
    )


@router.post("/customers/{customer_id}/ml/mode")
def set_customer_ml_mode(
    customer_id: uuid.UUID,
    network_code: str = Form(...),
    mode: MlScoringMode = Form(...),
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("admin:manage")),
    _csrf: None = Depends(verify_csrf),
) -> RedirectResponse:
    _get_customer_or_404(db, ctx.tenant_id, customer_id)
    customer_ml_service.set_mode(db, ctx.tenant_id, customer_id, network_code, mode)
    db.commit()
    return RedirectResponse(f"/ui/admin/customers/{customer_id}/ml?flash=Scoring+mode+updated.", status_code=303)


@router.post("/customers/{customer_id}/disposition")
def set_customer_disposition(
    customer_id: uuid.UUID,
    network_code: str = Form(...),
    mode: DispositionMode = Form(...),
    response_window_hours: str = Form(""),
    default_ach_return_reason_id: str = Form(""),
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("admin:manage")),
    _csrf: None = Depends(verify_csrf),
) -> RedirectResponse:
    _get_customer_or_404(db, ctx.tenant_id, customer_id)
    try:
        hours = int(response_window_hours) if response_window_hours else None
        if hours is not None and hours <= 0:
            raise ValueError("response_window_hours must be positive")
    except ValueError:
        return RedirectResponse(
            f"/ui/admin/customers/{customer_id}/ml?error={quote('Response window must be a positive whole number of hours, or blank.')}",
            status_code=303,
        )
    reason_id = uuid.UUID(default_ach_return_reason_id) if default_ach_return_reason_id else None
    auto_disposition_service.set_disposition_setting(
        db, ctx.tenant_id, customer_id, network_code,
        mode=mode, response_window_hours=hours, default_ach_return_reason_id=reason_id,
    )
    db.commit()
    return RedirectResponse(f"/ui/admin/customers/{customer_id}/ml?flash=Default+disposition+updated.", status_code=303)


@router.post("/customers/{customer_id}/ml/retrain")
def retrain_customer_model(
    customer_id: uuid.UUID,
    network_code: str = Form(...),
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("admin:manage")),
    _csrf: None = Depends(verify_csrf),
) -> RedirectResponse:
    _get_customer_or_404(db, ctx.tenant_id, customer_id)
    try:
        result = train_model(db, network_code, customer_id=customer_id)
    except (InsufficientTrainingData, RetrainCooldownActive) as exc:
        return RedirectResponse(f"/ui/admin/customers/{customer_id}/ml?error={quote(str(exc))}", status_code=303)
    flash = f"Retrained {network_code}: promoted={result.promoted}, metrics={result.metrics}"
    return RedirectResponse(f"/ui/admin/customers/{customer_id}/ml?flash={quote(flash)}", status_code=303)


@router.post("/customers/{customer_id}/ml/models/{model_id}/activate")
def activate_customer_model(
    customer_id: uuid.UUID,
    model_id: uuid.UUID,
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("admin:manage")),
    _csrf: None = Depends(verify_csrf),
) -> RedirectResponse:
    _get_customer_or_404(db, ctx.tenant_id, customer_id)
    try:
        activate_model(db, model_id, expected_customer_id=customer_id)
    except ValueError as exc:
        db.rollback()
        return RedirectResponse(f"/ui/admin/customers/{customer_id}/ml?error={quote(str(exc))}", status_code=303)
    db.commit()
    return RedirectResponse(f"/ui/admin/customers/{customer_id}/ml?flash=Model+activated.", status_code=303)
