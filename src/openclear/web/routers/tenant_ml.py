# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Settings → Fraud scoring: whether the bank's own model scores its items (SIMPLIFY_PLAN.md
SP-D10). Off by default. The score is advisory; it never decides an item. Turning it on is a
protected `sensitive` change (services/change_kinds.py "ml_scoring")."""


from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from openclear.config import get_settings
from openclear.db.session import get_db
from openclear.db.tenancy import TenantContext
from openclear.domain.tenant import Tenant
from openclear.services import audit_log_service
from openclear.web.deps import render_template, require_web_permission
from openclear.web.redirects import see_other
from openclear.web.security import verify_csrf
from openclear.web.errors import shown

router = APIRouter(prefix="/ui/settings/fraud-model", tags=["web-fraud-model"])
_PAGE = "/ui/settings/fraud-model"


@router.get("")
def fraud_model_page(
    request: Request, db: Session = Depends(get_db), ctx: TenantContext = Depends(require_web_permission("admin:manage"))
) -> HTMLResponse:
    tenant = db.get(Tenant, ctx.tenant_id)
    return render_template(
        request, "settings/fraud_model.html", ctx=ctx, enabled=tenant.ml_scoring_enabled,
        min_bank_decisions=get_settings().ml_bank_model_min_decisions,
    )


@router.post("")
def set_fraud_scoring(
    enabled: bool = Form(False),
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("admin:manage")),
    _csrf: None = Depends(verify_csrf),
) -> RedirectResponse:
    from openclear.services import change_request_service
    from openclear.web.routers.tenant_settings import waiting_message

    try:
        outcome = change_request_service.request_or_apply(db, "ml_scoring", {"enabled": enabled},
                                                          tenant_id=ctx.tenant_id, user_id=ctx.user_id)
    except ValueError as exc:
        db.rollback()
        return see_other(_PAGE, error=shown(exc))
    if outcome.waiting:
        db.commit()
        return see_other(_PAGE, flash=waiting_message(outcome))
    if outcome.summary != "Nothing changed.":
        audit_log_service.record_action(db, ctx.tenant_id, actor_user_id=ctx.user_id, channel="web", action="tenant.ml_scoring",
                                        summary=outcome.summary, resource_type="tenant", resource_id=ctx.tenant_id)
    db.commit()
    return see_other(_PAGE, flash="Fraud scoring turned on." if enabled else "Fraud scoring turned off.")
