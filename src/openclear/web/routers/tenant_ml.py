# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Settings → Fraud scoring model: a bank's choice between the shared network model and
a bank-only model, and its acknowledgement of how the shared model uses its data
(services/tenant_ml_service.py, FIX_PLAN.md Phase 5). tenant:manage only; locked in the
public demo (web/demo_guard.py). Every change is audit-logged."""


from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from openclear.config import get_settings
from openclear.db.session import get_db
from openclear.db.tenancy import TenantContext
from openclear.domain.tenant import MlModelSource, Tenant
from openclear.domain.user import User
from openclear.services import audit_log_service, tenant_ml_service
from openclear.web.deps import render_template, require_web_permission
from openclear.web.redirects import see_other
from openclear.web.security import verify_csrf

router = APIRouter(prefix="/ui/settings/fraud-model", tags=["web-fraud-model"])
_PAGE = "/ui/settings/fraud-model"


def _email(db: Session, user_id) -> str | None:
    user = db.get(User, user_id) if user_id else None
    return user.email if user else None


@router.get("")
def fraud_model_page(
    request: Request, db: Session = Depends(get_db), ctx: TenantContext = Depends(require_web_permission("tenant:manage"))
) -> HTMLResponse:
    tenant = db.get(Tenant, ctx.tenant_id)
    disclosure = get_settings().ml_shared_pool_disclosure_text
    return render_template(
        request,
        "settings/fraud_model.html",
        ctx=ctx,
        uses_bank_model=tenant.ml_model_source == MlModelSource.PRIVATE,
        changed_at=tenant.ml_source_changed_at,
        changed_by=_email(db, tenant.ml_source_changed_by_user_id),
        consent_at=tenant.ml_shared_consent_at,
        consent_by=_email(db, tenant.ml_shared_consent_by_user_id),
        switch_available_on=tenant_ml_service.private_switch_available_on(tenant),
        lock_days=get_settings().ml_new_bank_private_switch_lock_days,
        min_bank_decisions=get_settings().ml_bank_model_min_decisions,
        disclosure=disclosure,
        disclosure_is_placeholder=disclosure.startswith("PLACEHOLDER"),
        shared_summaries=tenant_ml_service.shared_model_summaries(db),
    )


def _audit(db: Session, ctx: TenantContext, action: str, summary: str) -> None:
    audit_log_service.record_action(
        db, ctx.tenant_id, actor_user_id=ctx.user_id, channel="web", action=action, summary=summary,
        resource_type="tenant", resource_id=ctx.tenant_id,
    )


@router.post("/switch-to-bank-only")
def switch_to_bank_only(
    confirm: bool = Form(False),
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("tenant:manage")),
    _csrf: None = Depends(verify_csrf),
) -> RedirectResponse:
    if not confirm:
        return see_other(_PAGE, error="Please confirm you understand what switching does.")
    try:
        seeded = tenant_ml_service.switch_to_bank_only(db, ctx.tenant_id, actor_user_id=ctx.user_id)
    except tenant_ml_service.SwitchNotAllowed as exc:
        db.rollback()
        return see_other(_PAGE, error=str(exc))
    copied = ", ".join(m.network_code for m in seeded) or "none (no shared model was active yet)"
    _audit(db, ctx, "tenant.ml_source_change", f"Switched to a bank-only fraud-scoring model (copied from shared: {copied})")
    db.commit()
    return see_other(_PAGE, flash="Switched to a bank-only model. It starts as a copy of the shared model.")


@router.post("/switch-to-shared")
def switch_to_shared(
    consent: bool = Form(False),
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("tenant:manage")),
    _csrf: None = Depends(verify_csrf),
) -> RedirectResponse:
    from openclear.services import change_request_service
    from openclear.web.routers.tenant_settings import waiting_message

    if not consent:
        return see_other(_PAGE, error="Please confirm you've read how the shared model uses your data.")
    try:
        # Pooling the bank's decisions waits for a second admin when the bank asks (DUAL_CONTROL_PLAN.md).
        outcome = change_request_service.request_or_apply(db, "ml.switch_to_shared", {"consented_by": str(ctx.user_id)},
                                                          tenant_id=ctx.tenant_id, user_id=ctx.user_id)
    except (tenant_ml_service.SwitchNotAllowed, ValueError) as exc:
        db.rollback()
        return see_other(_PAGE, error=str(exc))
    if outcome.waiting:
        db.commit()
        return see_other(_PAGE, flash=waiting_message(outcome))
    _audit(db, ctx, "tenant.ml_source_change", "Switched to the shared fraud-scoring model and acknowledged the data-pooling disclosure")
    db.commit()
    return see_other(_PAGE, flash="Switched to the shared model.")


@router.post("/consent")
def acknowledge_shared_pooling(
    consent: bool = Form(False),
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("tenant:manage")),
    _csrf: None = Depends(verify_csrf),
) -> RedirectResponse:
    if not consent:
        return see_other(_PAGE, error="Please tick the box to acknowledge it.")
    tenant_ml_service.record_shared_consent(db, ctx.tenant_id, actor_user_id=ctx.user_id)
    _audit(db, ctx, "tenant.ml_shared_consent", "Acknowledged how the shared fraud-scoring model uses this organization's data")
    db.commit()
    return see_other(_PAGE, flash="Acknowledgement recorded.")
