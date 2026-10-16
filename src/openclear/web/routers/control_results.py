# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Controls → a check's history, deviations and their responses (CONTROLS_PLAN.md CT2;
services/control_results_service.py). Reading needs what the Controls page needs
(audit_log:read); recording a response, the deviation settings and testing a past period
need compliance:manage."""

import uuid
from datetime import date, datetime, time, timedelta, timezone
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from openclear.db.session import get_db
from openclear.db.tenancy import TenantContext
from openclear.services import audit_log_service, control_results_service
from openclear.services.control_results_service import DeviationError
from openclear.web.deps import WebNotFound, render_template, require_web_permission
from openclear.web.security import verify_csrf

router = APIRouter(prefix="/ui/admin/controls", tags=["web-control-results"])
_read = require_web_permission("audit_log:read")
_manage = require_web_permission("compliance:manage")
MAX_PERIOD_DAYS = 400


def _titles() -> dict[str, str]:
    from openclear.compliance import checks

    return {c.key: c.title for c in checks.registry()}


def _back(path: str, message: str) -> RedirectResponse:
    return RedirectResponse(f"{path}?flash={quote(message)}", status_code=303)


# Literal paths before /deviations/{deviation_id}.


@router.get("/deviations")
def deviations(request: Request, db: Session = Depends(get_db), ctx: TenantContext = Depends(_read)) -> HTMLResponse:
    from openclear.compliance import checks
    from openclear.domain.tenant import Tenant

    tenant = db.get(Tenant, ctx.tenant_id)
    return render_template(request, "admin/control_deviations.html", ctx=ctx, rows=control_results_service.deviations(db, ctx.tenant_id),
                           titles=_titles(), grace_hours=tenant.control_grace_hours, warn_checks=set(tenant.control_warn_deviations or []),
                           checks=checks.registry(), today=datetime.now(timezone.utc).date())


@router.post("/deviations/settings")
async def deviation_settings(request: Request, db: Session = Depends(get_db), ctx: TenantContext = Depends(_manage),
                             _csrf: None = Depends(verify_csrf)) -> RedirectResponse:
    from openclear.compliance import checks
    from openclear.domain.tenant import Tenant

    form = await request.form()
    try:
        grace = int(str(form.get("grace_hours") or "24"))
    except ValueError:
        grace = -1
    if not 1 <= grace <= 24 * 30:
        return _back("/ui/admin/controls/deviations", "The grace period is 1 hour to 30 days.")
    known = {c.key for c in checks.registry()}
    warn = sorted(k for k in form.getlist("warn_check") if k in known)
    tenant = db.get(Tenant, ctx.tenant_id)
    tenant.control_grace_hours, tenant.control_warn_deviations = grace, warn
    audit_log_service.record_action(db, ctx.tenant_id, actor_user_id=ctx.user_id, channel="web", action="compliance.settings.changed",
                                    summary=f"Deviation grace period {grace} hours; warnings count for: {', '.join(warn) or 'none'}",
                                    resource_type="tenant", resource_id=ctx.tenant_id)
    db.commit()
    return _back("/ui/admin/controls/deviations", "Deviation settings saved.")


@router.post("/test-period")
def test_period(start: date = Form(...), end: date = Form(...), db: Session = Depends(get_db), ctx: TenantContext = Depends(_manage),
                _csrf: None = Depends(verify_csrf)) -> RedirectResponse:
    """Every population check over a past period (CT-D11), recorded as this person's run."""
    if end < start or (end - start).days > MAX_PERIOD_DAYS or end > datetime.now(timezone.utc).date():
        return _back("/ui/admin/controls/deviations", f"Choose a past period of at most {MAX_PERIOD_DAYS} days.")
    begin = datetime.combine(start, time.min, tzinfo=timezone.utc)
    finish = datetime.combine(end, time.min, tzinfo=timezone.utc) + timedelta(days=1)
    results = control_results_service.test_period(db, ctx.tenant_id, begin, finish, user_id=ctx.user_id)
    audit_log_service.record_action(db, ctx.tenant_id, actor_user_id=ctx.user_id, channel="web", action="compliance.period_tested",
                                    summary=f"Tested the population checks over {start} to {end}: "
                                            + ", ".join(f"{r.check_key} {r.status}" for r in results),
                                    resource_type="tenant", resource_id=ctx.tenant_id)
    db.commit()
    failing = [r for r in results if r.status != "PASS"]
    return _back("/ui/admin/controls/deviations", f"Tested {start} to {end}: {len(results) - len(failing)} pass, {len(failing)} don't.")


@router.get("/deviations/{deviation_id}")
def deviation(request: Request, deviation_id: uuid.UUID, db: Session = Depends(get_db), ctx: TenantContext = Depends(_read)) -> HTMLResponse:
    from openclear.domain.control_result import ControlTestResult
    from openclear.domain.user import User

    found = control_results_service.get_deviation(db, ctx.tenant_id, deviation_id)
    if found is None:
        raise WebNotFound()
    opened = db.get(ControlTestResult, found.opened_result_id)
    closed = db.get(ControlTestResult, found.closed_result_id) if found.closed_result_id else None
    responder = db.get(User, found.responded_by_user_id) if found.responded_by_user_id else None
    return render_template(request, "admin/control_deviation.html", ctx=ctx, deviation=found, opened=opened, closed=closed,
                           title=_titles().get(found.check_key, found.check_key), responder=responder.email if responder else None)


@router.post("/deviations/{deviation_id}/respond")
def respond(deviation_id: uuid.UUID, cause: str = Form(""), response: str = Form(""), db: Session = Depends(get_db),
            ctx: TenantContext = Depends(_manage), _csrf: None = Depends(verify_csrf)) -> RedirectResponse:
    path = f"/ui/admin/controls/deviations/{deviation_id}"
    try:
        found = control_results_service.respond(db, ctx.tenant_id, deviation_id, cause=cause, response=response, user_id=ctx.user_id)
    except DeviationError as exc:
        db.rollback()
        if control_results_service.get_deviation(db, ctx.tenant_id, deviation_id) is None:
            raise WebNotFound() from None
        return _back(path, str(exc))
    audit_log_service.record_action(db, ctx.tenant_id, actor_user_id=ctx.user_id, channel="web", action="compliance.deviation.responded",
                                    summary=f"Recorded the cause of and response to the {found.check_key} deviation opened "
                                            f"{found.opened_at:%Y-%m-%d %H:%M}", resource_type="control_deviation", resource_id=found.id)
    db.commit()
    return _back(path, "Response recorded.")


@router.get("/checks/{check_key}")
def check_history(request: Request, check_key: str, db: Session = Depends(get_db), ctx: TenantContext = Depends(_read)) -> HTMLResponse:
    from openclear.compliance import checks, controls

    check = checks.by_key().get(check_key)
    if check is None:
        raise WebNotFound()
    results = control_results_service.history(db, ctx.tenant_id, check_key)
    return render_template(request, "admin/control_check.html", ctx=ctx, check=check, results=results[:100],
                           strip=control_results_service.daily_strip(results),
                           deviations=control_results_service.deviations(db, ctx.tenant_id, check_key=check_key, limit=50),
                           tested_controls=controls.for_check(check_key))
