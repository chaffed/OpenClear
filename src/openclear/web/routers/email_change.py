# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed
"""Confirming a new email address (OWASP_PLAN.md OW-D31; services/email_change_service.py).
No sign-in: the link, sent only to the new address, is the proof. The page asks for a press
of Confirm, so a mail scanner that opens links can't make the change."""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy.orm import Session

from openclear.db.session import get_db
from openclear.services import email_change_service
from openclear.web.deps import render_template
from openclear.web.rate_limit import rate_limit
from openclear.web.security import verify_csrf

router = APIRouter(tags=["web-email-change"])
_limit = rate_limit("email_change", limit_setting="oauth_token_rate_limit_per_minute")


def _page(request: Request, opened, *, done: bool = False, error: str | None = None, status_code: int = 200) -> HTMLResponse:
    response = render_template(request, "security/email_change.html", opened=opened, done=done, error=error, status_code=status_code)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"  # the link is a secret
    return response


@router.get("/ui/email-change/{token}")
def confirm_form(request: Request, token: str, db: Session = Depends(get_db), _rl: None = Depends(_limit)) -> HTMLResponse:
    return _page(request, email_change_service.open_change(db, token))


@router.post("/ui/email-change/{token}")
def confirm(request: Request, token: str, db: Session = Depends(get_db), _rl: None = Depends(_limit),
            _csrf: None = Depends(verify_csrf)) -> Response:
    opened = email_change_service.open_change(db, token)
    if opened is None:
        return _page(request, None, status_code=410)
    try:
        email_change_service.confirm(db, opened)
    except email_change_service.EmailChangeError as exc:
        db.rollback()
        return _page(request, opened, error=str(exc), status_code=422)
    db.commit()
    return _page(request, opened, done=True)
