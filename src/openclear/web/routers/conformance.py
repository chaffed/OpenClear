# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Settings → Integration checklist (services/conformance_service.py)."""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from openclear.db.session import get_db
from openclear.db.tenancy import TenantContext
from openclear.services import conformance_service
from openclear.web.deps import render_template, require_web_permission
from openclear.web.security import verify_csrf
from openclear.web.redirects import see_other

router = APIRouter(prefix="/ui/admin/integration", tags=["web-conformance"])
_manage = require_web_permission("api_client:manage")


@router.get("")
def checklist_page(request: Request, db: Session = Depends(get_db), ctx: TenantContext = Depends(_manage)) -> HTMLResponse:
    summary = conformance_service.summary(conformance_service.checklist(db, ctx.tenant_id))
    return render_template(request, "admin/integration.html", ctx=ctx, summary=summary)


@router.post("/probe")
def run_probe(db: Session = Depends(get_db), ctx: TenantContext = Depends(_manage), _csrf: None = Depends(verify_csrf)) -> RedirectResponse:
    conformance_service.probe_webhooks(db, ctx.tenant_id)
    db.commit()
    return see_other("/ui/admin/integration")
