# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import uuid

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from openclear.config import get_settings
from openclear.db.session import get_db
from openclear.db.tenancy import TenantContext
from openclear.domain.customer_disposition_setting import DispositionMode
from openclear.domain.ml_model import MlModel
from openclear.domain.tenant import Tenant
from openclear.ml.registry import activate_model
from openclear.services import change_request_service
from openclear.ml.train import InsufficientTrainingData, RetrainCooldownActive, count_labeled_decisions, train_model
from openclear.networks.registry import registered_codes
from openclear.services import (
    ach_return_reason_service,
    audit_log_service,
    auto_disposition_service,
    customer_service,
    demo_tenant_service,
    sso_service,
)
from openclear.web.deps import WebNotFound, render_template, require_any_web_permission, require_web_permission
from openclear.web.redirects import see_other
from openclear.web.security import clear_auth_cookies, verify_csrf

router = APIRouter(prefix="/ui/admin", tags=["web-admin"])


@router.get("")
def admin_home(
    ctx: TenantContext = Depends(require_any_web_permission("admin:manage", "tenant:manage")),
) -> RedirectResponse:
    """Admin and Settings are one control panel now (UX_PLAN.md U11)."""
    return see_other("/ui/settings")


@router.get("/ml-models")
def ml_models(
    request: Request, db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("admin:manage")),
) -> HTMLResponse:
    """The bank's own fraud-scoring models (SIMPLIFY_PLAN.md SP-D8), and how many of its own final
    decisions each network has to learn from."""
    tenant = db.get(Tenant, ctx.tenant_id)
    models = db.execute(select(MlModel).where(MlModel.tenant_id == ctx.tenant_id)
                        .order_by(MlModel.network_code, MlModel.created_at.desc())).scalars().all()
    decision_counts = {network_code: count_labeled_decisions(db, network_code, tenant_id=ctx.tenant_id)
                       for network_code in registered_codes()}
    return render_template(
        request, "admin/ml_models.html", ctx=ctx, models=models, networks=registered_codes(), decision_counts=decision_counts,
        scoring_enabled=tenant.ml_scoring_enabled, min_bank_decisions=get_settings().ml_bank_model_min_decisions,
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
    response = see_other("/ui/login", flash="Demo data reset — log back in with the demo credentials.")
    clear_auth_cookies(response)
    return response


@router.post("/ml/retrain")
def retrain(
    network_code: str = Form(...),
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("admin:manage")),
    _csrf: None = Depends(verify_csrf),
) -> RedirectResponse:
    if not db.get(Tenant, ctx.tenant_id).ml_scoring_enabled:
        return see_other("/ui/admin/ml-models", error="Turn on fraud scoring first (Settings → Fraud scoring).")
    try:
        result = train_model(db, network_code, tenant_id=ctx.tenant_id)
    except (InsufficientTrainingData, RetrainCooldownActive) as exc:
        return see_other("/ui/admin/ml-models", error=str(exc))
    audit_log_service.record_action(
        db, ctx.tenant_id, actor_user_id=ctx.user_id, channel="web", action="ml_model.retrain",
        summary=f"Retrained the bank's {network_code} model ({'activated' if result.promoted else 'not activated'})",
        resource_type="ml_model", resource_id=result.model_row.id,
    )
    db.commit()
    reason = (result.model_row.metrics_json or {}).get("evaluation", {}).get("reason", "")
    return see_other("/ui/admin/ml-models", flash=f'Retrained {network_code}. {reason}')


@router.post("/ml/models/{model_id}/activate")
def activate(
    model_id: uuid.UUID,
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("admin:manage")),
    _csrf: None = Depends(verify_csrf),
) -> RedirectResponse:
    if change_request_service.needs_approval(db, ctx.tenant_id, "ml_model.activate"):
        try:
            change_request_service.request_model_activation(db, ctx.tenant_id, model_id, user_id=ctx.user_id)
        except change_request_service.ChangeRequestError as exc:
            db.rollback()
            return see_other("/ui/admin/ml-models", error=str(exc) if "waiting" in str(exc) else "That model couldn't be found.")
        db.commit()
        return see_other("/ui/admin/ml-models", flash="Waiting for a second admin to approve (Settings → Changes awaiting approval).")
    try:
        # Confined to this bank's own models (ml/registry.py::activate_model).
        model = activate_model(db, model_id, expected_tenant_id=ctx.tenant_id)
    except ValueError:
        db.rollback()
        return see_other("/ui/admin/ml-models", error="That model couldn't be found.")
    audit_log_service.record_action(
        db, ctx.tenant_id, actor_user_id=ctx.user_id, channel="web", action="ml_model.activate",
        summary=f"Activated the bank's {model.network_code} model {model.version}", resource_type="ml_model", resource_id=model.id,
    )
    db.commit()
    return see_other("/ui/admin/ml-models", flash="Model activated.")


def _get_customer_or_404(db: Session, tenant_id: uuid.UUID, customer_id: uuid.UUID):
    customer = customer_service.get_customer(db, tenant_id, customer_id)
    if customer is None:
        raise WebNotFound()
    return customer


@router.get("/customers/{customer_id}/ml")
def customer_ml_moved(customer_id: uuid.UUID) -> RedirectResponse:
    """Customers have no models of their own now (SIMPLIFY_PLAN.md SP-D7): their page is the default decision."""
    return see_other(f"/ui/admin/customers/{customer_id}/default-decision")


@router.get("/customers/{customer_id}/default-decision")
def customer_default_decision(
    request: Request,
    customer_id: uuid.UUID,
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("admin:manage")),
) -> HTMLResponse:
    customer = _get_customer_or_404(db, ctx.tenant_id, customer_id)
    return render_template(
        request, "admin/customer_default_decision.html", ctx=ctx, customer=customer,
        disposition_summary=auto_disposition_service.get_disposition_summary(db, ctx.tenant_id, customer_id),
        disposition_modes=list(DispositionMode),
        ach_return_reasons=ach_return_reason_service.list_ach_return_reasons(db, ctx.tenant_id, active_only=True),
    )


@router.post("/customers/{customer_id}/disposition")
def set_customer_disposition(
    customer_id: uuid.UUID,
    network_code: str = Form(...),
    mode: DispositionMode = Form(...),
    default_ach_return_reason_id: str = Form(""),
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("admin:manage")),
    _csrf: None = Depends(verify_csrf),
) -> RedirectResponse:
    """A customer's default decision (FULL_REVIEW_PLAN.md FR4): applied at once when it doesn't
    pay anything, otherwise waiting for a second admin under the bank's rule."""
    from openclear.web.routers.tenant_settings import waiting_message

    back = f"/ui/admin/customers/{customer_id}/default-decision"
    customer = _get_customer_or_404(db, ctx.tenant_id, customer_id)
    try:
        reason_id = str(uuid.UUID(default_ach_return_reason_id)) if default_ach_return_reason_id else None
    except ValueError:
        return see_other(back, error="Choose a return reason from the list.")
    try:
        outcome = change_request_service.request_or_apply(
            db, "customer.disposition", {"customer_id": str(customer_id), "network_code": network_code, "mode": mode.value,
                                         "ach_return_reason_id": reason_id},
            tenant_id=ctx.tenant_id, user_id=ctx.user_id,
            summary=f"Default decision for {network_code}: {mode.value.replace('_', ' ')} for {customer.name}")
    except ValueError as exc:
        db.rollback()
        return see_other(back, error=str(exc))
    if outcome.waiting:
        db.commit()
        return see_other(back, flash=waiting_message(outcome))
    if outcome.summary != "Nothing changed.":
        audit_log_service.record_action(db, ctx.tenant_id, actor_user_id=ctx.user_id, channel="web", action="customer.update_disposition",
                                        summary=outcome.summary, resource_type="customer", resource_id=customer_id)
    db.commit()
    return see_other(back, flash="Default disposition updated.")
