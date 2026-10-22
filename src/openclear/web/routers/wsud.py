# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""The bank's Written Statement of Unauthorized Debit form, as its customers see it (SIMPLIFY_PLAN.md
SP1). OpenClear shows the form; the bank collects and keeps the signed statement its own way."""

from urllib.parse import quote

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, Response, StreamingResponse
from sqlalchemy.orm import Session

from openclear import storage
from openclear.db.session import get_db
from openclear.db.tenancy import TenantContext
from openclear.services import bank_form_service
from openclear.web.deps import WebNotFound, render_template, require_web_permission

router = APIRouter(tags=["web-wsud"])
_read = require_web_permission("ach_transaction:read")


@router.get("/ui/wsud")
def wsud_form(request: Request, db: Session = Depends(get_db), ctx: TenantContext = Depends(_read)) -> HTMLResponse:
    return render_template(request, "wsud/form.html", ctx=ctx, form=bank_form_service.get(db, ctx.tenant_id))


@router.get("/ui/wsud/form.pdf")
def wsud_form_pdf(db: Session = Depends(get_db), ctx: TenantContext = Depends(_read)) -> Response:
    form = bank_form_service.get(db, ctx.tenant_id)
    if form is None or form.format != "pdf" or not form.storage_ref:
        raise WebNotFound()
    return StreamingResponse(
        storage.iter_chunks(form.storage_ref), media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename=\"form.pdf\"; filename*=UTF-8''{quote(form.filename or 'form.pdf')}",
                 "X-Content-Type-Options": "nosniff"},
    )
