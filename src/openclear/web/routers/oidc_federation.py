# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""OIDC endpoints an identity provider calls (SDK_PLAN.md phase F), beside sign-in itself
(web/routers/auth.py):

- **Back-channel logout** (OpenID Connect Back-Channel Logout 1.0): the provider POSTs a
  signed logout token when someone signs out there; OpenClear ends that person's sessions.
- **Third-party initiated login** (OpenID Connect Core §4): a tile in the provider's portal
  sends the browser here, and OpenClear starts a normal sign-in with that provider (with
  PKCE, state and nonce, so nothing is taken on trust from the link itself).

The bank and connection are in the URL, since these arrive before any session exists."""

import logging
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode, urlsplit

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from sqlalchemy.orm import Session

from openclear.auth.oidc_service import OidcError, verify_logout_token
from openclear.config import get_settings
from openclear.db.session import get_db
from openclear.db.tenancy import bind_session_to_tenant
from openclear.domain.sso_connection import SsoProtocol
from openclear.services import audit_log_service, session_service, sso_service
from openclear.web.deps import WebNotFound
from openclear.web.security import safe_next_path

router = APIRouter(prefix="/ui/sso/oidc", tags=["web-oidc"], include_in_schema=False)
logger = logging.getLogger(__name__)


def _connection(db: Session, tenant_id: uuid.UUID, connection_id: uuid.UUID):
    bind_session_to_tenant(db, tenant_id)
    connection = sso_service.get_connection(db, tenant_id, connection_id)
    if connection is None or not connection.is_active or connection.protocol != SsoProtocol.OIDC:
        raise WebNotFound()
    return connection


def _no_store(response: Response) -> Response:
    response.headers["Cache-Control"] = "no-store"
    return response


@router.post("/{tenant_id}/{connection_id}/backchannel-logout")
def backchannel_logout(
    tenant_id: uuid.UUID, connection_id: uuid.UUID, logout_token: str = Form(...), db: Session = Depends(get_db),
) -> Response:
    try:
        connection = _connection(db, tenant_id, connection_id)
    except WebNotFound:
        return _no_store(JSONResponse({"error": "invalid_request"}, status_code=400))
    try:
        claims = verify_logout_token(connection, logout_token)
        expires = datetime.fromtimestamp(float(claims["iat"]), timezone.utc) + timedelta(minutes=20)
        sso_service.record_assertion(db, tenant_id, connection, f"logout:{claims['jti']}", expires)
    except (OidcError, sso_service.Replayed) as exc:
        db.rollback()
        logger.warning("Back-channel logout refused for connection %s: %s", connection.id, exc)
        return _no_store(JSONResponse({"error": "invalid_request", "error_description": "The logout token was rejected."}, status_code=400))
    user = sso_service.find_identity_user(db, connection, str(claims["sub"]))
    if user is not None:
        session_service.revoke_all_sessions(db, user)
        audit_log_service.record_action(
            db, tenant_id, actor_user_id=user.id, channel="web", action="user.logout_sso",
            summary=f"Signed out by the identity provider ({connection.display_name})", resource_type="user", resource_id=user.id,
        )
    db.commit()  # the used jti is kept even when the person isn't known here
    return _no_store(Response(status_code=200))


@router.get("/{tenant_id}/{connection_id}/initiate")
def initiate_login(
    request: Request, tenant_id: uuid.UUID, connection_id: uuid.UUID, iss: str, target_link_uri: str | None = None,
    login_hint: str | None = None, db: Session = Depends(get_db),
) -> Response:
    """Starts sign-in with this connection's provider. `iss` must be the provider's issuer;
    `target_link_uri` is only followed to a page on this site."""
    connection = _connection(db, tenant_id, connection_id)
    if iss.rstrip("/") != connection.issuer.rstrip("/"):
        logger.warning("Third-party initiated login for connection %s named issuer %r", connection.id, iss)
        raise WebNotFound()
    next_path = "/ui/"
    if target_link_uri:
        target = urlsplit(target_link_uri)
        public = urlsplit(get_settings().public_base_url or str(request.base_url))
        same_site = not target.netloc or (target.scheme, target.netloc) == (public.scheme, public.netloc)
        if same_site:
            next_path = safe_next_path(target.path + (f"?{target.query}" if target.query else ""))
    params = {"tenant_id": str(tenant_id), "next": next_path}
    if login_hint:
        params["login_hint"] = login_hint
    return RedirectResponse(f"/ui/login/sso/{connection.id}/start?{urlencode(params)}", status_code=303)

