# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""The embedded session's own pages (services/embed_service.py): the launch link an online
banking page opens in its frame, and the page a frame lands on when its session ends."""

import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.orm import Session

from openclear.auth.security import create_session_tokens
from openclear.db.session import get_db
from openclear.db.tenancy import bind_session_to_tenant
from openclear.domain.tenant import Tenant
from openclear.services import audit_log_service, embed_service, user_service
from openclear.web.deps import render_template
from openclear.web.security import set_session_cookies

router = APIRouter(prefix="/ui/embed", tags=["web-embed"])
logger = logging.getLogger(__name__)


def _ended(request: Request, message: str, status_code: int = 200) -> HTMLResponse:
    # Framable by anyone: it says only that the session ended and tells the frame's parent
    # so (no data, no actions), so the online banking page can start a new one.
    request.state.frame_ancestor = "*"
    return render_template(request, "embed/ended.html", ctx=None, message=message, status_code=status_code)


@router.get("/launch")
def launch(request: Request, code: str = "", db: Session = Depends(get_db)) -> Response:
    tenant_id = embed_service.tenant_of(code)
    if tenant_id is None:
        return _ended(request, "This link isn't valid.", 400)
    bind_session_to_tenant(db, tenant_id)
    try:
        launch_row, user, membership = embed_service.redeem(db, code)
    except embed_service.EmbedError as exc:
        db.commit()  # a used code stays used
        return _ended(request, str(exc), 400)
    tenant = db.get(Tenant, tenant_id)
    tokens = create_session_tokens(
        user_id=user.id, tenant_id=tenant_id, security_group_id=membership.security_group_id, customer_id=membership.customer_id,
        token_version=user.token_version, access_token_expire_minutes=tenant.access_token_expire_minutes,
        refresh_token_expire_minutes=tenant.refresh_token_expire_minutes, embed={"po": launch_row.parent_origin},
    )
    user_service.record_login(db, user.id)
    audit_log_service.record_action(
        db, tenant_id, actor_user_id=user.id, channel="web", action="user.login_embed",
        summary=f"Signed in inside online banking ({launch_row.parent_origin})", resource_type="user", resource_id=user.id,
    )
    db.commit()
    response = RedirectResponse(launch_row.path, status_code=303)
    set_session_cookies(response, tokens)
    return response


@router.get("/ended")
def ended(request: Request) -> HTMLResponse:
    return _ended(request, "Your session has ended.")
