# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Settings → Support access (UX_PLAN.md U8; services/support_access_service.py): the bank
grants a named platform support person access for a reason and a period, approves write
access with a second admin, and ends access at any time."""

import uuid
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from openclear.db.session import get_db
from openclear.db.tenancy import TenantContext
from openclear.domain.user import User
from openclear.services import support_access_service
from openclear.web.deps import render_template, require_web_permission
from openclear.web.security import verify_csrf
from openclear.web.redirects import see_other

router = APIRouter(prefix="/ui/settings/support-access", tags=["web-support-access"])
_PERMISSION = "tenant:manage"


def _back(message: str, *, error: bool = False) -> RedirectResponse:
    return see_other(f"/ui/settings/support-access?{'error' if error else 'flash'}={quote(message)}")


@router.get("")
def page(request: Request, db: Session = Depends(get_db), ctx: TenantContext = Depends(require_web_permission(_PERMISSION))) -> HTMLResponse:
    grants = support_access_service.for_bank(db, ctx.tenant_id)
    db.commit()  # expiries noticed just now are recorded
    ids = {g.support_user_id for g in grants} | {g.granted_by_user_id for g in grants} | {g.approved_by_user_id for g in grants if g.approved_by_user_id}
    emails = dict(db.execute(select(User.id, User.email).where(User.id.in_(ids))).all()) if ids else {}
    return render_template(request, "settings/support_access.html", ctx=ctx, grants=grants, emails=emails,
                           people=support_access_service.support_people(db), max_hours=support_access_service.MAX_HOURS)


@router.post("")
def grant(support_email: str = Form(...), reason: str = Form(...), hours: int = Form(4), write_access: bool = Form(False),
          db: Session = Depends(get_db), ctx: TenantContext = Depends(require_web_permission(_PERMISSION)),
          _csrf: None = Depends(verify_csrf)) -> RedirectResponse:
    try:
        row = support_access_service.grant(db, ctx.tenant_id, support_email=support_email, reason=reason, hours=hours,
                                           write_access=write_access, granted_by_user_id=ctx.user_id)
    except support_access_service.SupportAccessError as exc:
        db.rollback()
        return _back(str(exc), error=True)
    db.commit()
    return _back("Write access granted; a second admin must approve it before it starts." if row.write_access
                 else "Access granted.")


@router.post("/{grant_id}/{verdict}")
def decide(grant_id: uuid.UUID, verdict: str, db: Session = Depends(get_db), ctx: TenantContext = Depends(require_web_permission(_PERMISSION)),
           _csrf: None = Depends(verify_csrf)) -> RedirectResponse:
    try:
        if verdict == "approve":
            support_access_service.approve(db, ctx.tenant_id, grant_id, approver_user_id=ctx.user_id)
            message = "Write access approved."
        elif verdict == "revoke":
            support_access_service.revoke(db, ctx.tenant_id, grant_id, user_id=ctx.user_id)
            message = "Access ended."
        else:
            return _back("Unknown action.", error=True)
    except support_access_service.SupportAccessError as exc:
        db.rollback()
        return _back(str(exc), error=True)
    db.commit()
    return _back(message)
