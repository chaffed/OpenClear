# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""/ui/sandbox: a vendor gets a seeded sandbox bank (services/sandbox_service.py). Only on an
installation run as a public sandbox."""

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from openclear.config import get_settings
from openclear.db.session import get_db
from openclear.services import sandbox_service
from openclear.web.deps import WebNotFound, render_template
from openclear.web.rate_limit import rate_limit
from openclear.web.security import verify_csrf

router = APIRouter(prefix="/ui/sandbox", tags=["web-sandbox"])


def _enabled() -> None:
    if not get_settings().sandbox_signup_enabled:
        raise WebNotFound()


@router.get("")
def sandbox_form(request: Request, _on: None = Depends(_enabled)) -> HTMLResponse:
    return render_template(request, "sandbox/signup.html", ctx=None, lifetime_days=get_settings().sandbox_lifetime_days)


@router.post("")
def create_sandbox(
    request: Request,
    organization: str = Form(""),
    contact_email: str = Form(""),
    accept: bool = Form(False),
    db: Session = Depends(get_db),
    _on: None = Depends(_enabled),
    _csrf: None = Depends(verify_csrf),
    _limit: None = Depends(rate_limit("sandbox_signup", limit_setting="sandbox_signups_per_hour", window_seconds=3600)),
) -> HTMLResponse:
    settings = get_settings()
    values = {"organization": organization, "contact_email": contact_email}
    if not accept:
        return render_template(request, "sandbox/signup.html", ctx=None, lifetime_days=settings.sandbox_lifetime_days, values=values,
                               error="Please accept the sandbox terms.", status_code=422)
    try:
        sandbox = sandbox_service.create(db, organization=organization, contact_email=contact_email)
    except sandbox_service.SandboxError as exc:
        db.rollback()
        return render_template(request, "sandbox/signup.html", ctx=None, lifetime_days=settings.sandbox_lifetime_days, values=values,
                               error=str(exc), status_code=422)
    base = (settings.public_base_url or str(request.base_url)).rstrip("/")
    response = render_template(request, "sandbox/ready.html", ctx=None, sandbox=sandbox, base=base)
    response.headers["Cache-Control"] = "no-store"  # secrets on the page
    return response
