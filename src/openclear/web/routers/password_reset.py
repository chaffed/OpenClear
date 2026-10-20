# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed
"""Finishing an administrator's password reset (services/password_reset_service.py): no
sign-in, the emailed link is the credential, as with invitations (web/routers/people.py)."""


from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy.orm import Session

from openclear.db.session import get_db
from openclear.services import audit_log_service, password_reset_service
from openclear.web.deps import render_template
from openclear.web.rate_limit import rate_limit
from openclear.web.redirects import see_other
from openclear.web.security import verify_csrf

router = APIRouter(tags=["web-password-reset"])

_limit = rate_limit("password_reset", limit_setting="oauth_token_rate_limit_per_minute")


def _page(request: Request, opened, *, error: str | None = None, status_code: int = 200) -> HTMLResponse:
    response = render_template(request, "auth/password_reset.html", opened=opened, error=error, status_code=status_code)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"  # the link is a secret
    return response


@router.get("/ui/password-reset/{token}")
def reset_form(request: Request, token: str, db: Session = Depends(get_db), _rl: None = Depends(_limit)) -> HTMLResponse:
    return _page(request, password_reset_service.open_reset(db, token))


@router.post("/ui/password-reset/{token}")
def reset(request: Request, token: str, password: str = Form(""), password_confirm: str = Form(""),
          db: Session = Depends(get_db), _rl: None = Depends(_limit), _csrf: None = Depends(verify_csrf)) -> Response:
    opened = password_reset_service.open_reset(db, token)
    if opened is None:
        return _page(request, None, status_code=410)
    if password != password_confirm:
        return _page(request, opened, error="The two passwords don't match.", status_code=422)
    try:
        user = password_reset_service.complete(db, opened, password)
    except password_reset_service.ResetError as exc:
        return _page(request, opened, error=str(exc), status_code=422)
    audit_log_service.record_action(db, opened.tenant.id, actor_user_id=user.id, channel="web",
                                    action="user.password_reset_completed",
                                    summary=f"{user.email} chose a new password from a reset link",
                                    resource_type="user", resource_id=user.id)
    db.commit()
    return see_other(f"/ui/login/{opened.tenant.slug}", flash="Your password is set. Sign in to continue.")
