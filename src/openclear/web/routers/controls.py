# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Admin → Controls (services/controls_service.py; COMPLIANCE_PLAN.md C4)."""

from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.orm import Session

from openclear.db.session import get_db
from openclear.db.tenancy import TenantContext
from openclear.services import controls_service
from openclear.web.deps import render_template, require_web_permission
from openclear.web.security import verify_csrf

router = APIRouter(tags=["web-controls"])


@router.get("/ui/admin/controls")
def controls_page(request: Request, db: Session = Depends(get_db),
                  ctx: TenantContext = Depends(require_web_permission("audit_log:read"))) -> HTMLResponse:
    today = datetime.now(timezone.utc).date()  # export periods are UTC days
    return render_template(request, "admin/controls.html", ctx=ctx, result=controls_service.summary(controls_service.status(db, ctx.tenant_id)),
                           default_start=today.replace(day=1).isoformat(), default_end=today.isoformat())


@router.post("/ui/admin/controls/evidence")
def export_evidence(start: date = Form(...), end: date = Form(...), format: str = Form("pdf"), db: Session = Depends(get_db),
                    ctx: TenantContext = Depends(require_web_permission("audit_log:read")), _csrf: None = Depends(verify_csrf)) -> Response:
    from urllib.parse import quote

    from openclear.api.v1.audit import export_response

    try:
        return export_response(db, ctx, start, end, "json" if format == "json" else "pdf", "web")
    except HTTPException as exc:
        return RedirectResponse(f"/ui/admin/controls?error={quote(str(exc.detail))}", status_code=303)
