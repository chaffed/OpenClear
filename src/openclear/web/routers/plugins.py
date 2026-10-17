# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Settings → Plugins (PLUGIN_PLAN.md): what's loaded, from where and what each provides, and
which plugin import formats and decision layouts this bank has turned on."""

from urllib.parse import quote_plus

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from openclear import plugins
from openclear.db.session import get_db
from openclear.db.tenancy import TenantContext
from openclear.services import audit_log_service, change_request_service, plugin_service
from openclear.web.deps import render_template, require_web_permission
from openclear.web.security import verify_csrf

router = APIRouter(prefix="/ui/admin/plugins", tags=["web-plugins"])
_manage = require_web_permission("admin:manage")

POINT_LABELS = {
    "import_formats": "Import format", "decision_layouts": "Decision file layout", "matching_rules": "Matching rule",
    "notification_channels": "Notification channel", "ui_slots": "Page addition",
}


@router.get("")
def list_plugins(request: Request, db: Session = Depends(get_db), ctx: TenantContext = Depends(_manage)) -> HTMLResponse:
    return render_template(
        request, "admin/plugins.html", ctx=ctx, plugins=plugins.status(), api_version=plugins.PLUGIN_API_VERSION,
        choices=plugin_service.choices(db, ctx.tenant_id), point_labels=POINT_LABELS,
    )


def _waiting(outcome) -> str:
    if not outcome.waiting:
        return outcome.summary
    if outcome.change.applies_at:
        return "Nobody else can approve this, so it applies in 24 hours unless you withdraw it (Settings → Changes awaiting approval)."
    return "Waiting for a second admin to approve (Settings → Changes awaiting approval)."


@router.post("/choices")
async def set_choice(
    request: Request, point: str = Form(...), name: str = Form(...), enabled: bool = Form(False),
    db: Session = Depends(get_db), ctx: TenantContext = Depends(_manage), _csrf: None = Depends(verify_csrf),
) -> RedirectResponse:
    form = await request.form()
    config = {key.removeprefix("config_"): str(value) for key, value in form.items() if key.startswith("config_")}
    if point == "matching_rules" and change_request_service.needs_approval(db, ctx.tenant_id, "matching_rule.set"):
        # Changes to how items are judged need a second admin when the bank asks (DUAL_CONTROL_PLAN.md).
        try:
            outcome = change_request_service.request_or_apply(
                db, "matching_rule.set", {"name": name, "enabled": enabled, "config": config}, tenant_id=ctx.tenant_id, user_id=ctx.user_id,
                summary=f"Turn {'on' if enabled else 'off'} the matching rule {name}")
        except change_request_service.ChangeRequestError as exc:
            db.rollback()
            return RedirectResponse(f"/ui/admin/plugins?flash={quote_plus(str(exc))}", status_code=303)
        db.commit()
        return RedirectResponse("/ui/admin/plugins?flash=" + quote_plus(_waiting(outcome)), status_code=303)
    try:
        changed = plugin_service.set_enabled(db, ctx.tenant_id, point, name, enabled, user_id=ctx.user_id, config=config)
    except ValueError as exc:
        db.rollback()
        return RedirectResponse(f"/ui/admin/plugins?flash={quote_plus(str(exc))}", status_code=303)
    if changed:
        audit_log_service.record_action(
            db, ctx.tenant_id, actor_user_id=ctx.user_id, channel="web", action="plugin.enable" if enabled else "plugin.disable",
            summary=f"Turned {'on' if enabled else 'off'} the {POINT_LABELS.get(point, point).lower()} {name}"
                    + (" (settings saved)" if enabled and config else ""),
            resource_type="tenant_extension", metadata={"point": point, "name": name},
        )
    db.commit()
    return RedirectResponse(f"/ui/admin/plugins?flash={'Turned+on' if enabled else 'Turned+off'}.", status_code=303)
