# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""SAML endpoints (auth/saml_service.py): metadata, the assertion consumer service and
single logout. The bank and connection are in every URL, since the identity provider
calls these before any session exists (see web/routers/auth.py's note on RLS)."""

import logging
import secrets
import uuid

import jwt
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.orm import Session

from openclear.auth import saml_service
from openclear.auth.oidc_service import OidcClaims
from openclear.auth.security import decode_sso_state_token
from openclear.db.session import get_db
from openclear.db.tenancy import bind_session_to_tenant
from openclear.domain.sso_connection import SsoProtocol
from openclear.services import audit_log_service, session_service, sso_service
from openclear.web.deps import WebNotFound, render_template
from openclear.web.routers.auth import _SSO_FAILED_MESSAGE, finish_sso_login
from openclear.web.security import SAML_STATE_COOKIE_NAME, clear_saml_state_cookie

router = APIRouter(prefix="/ui/sso/saml", tags=["web-saml"], include_in_schema=False)
logger = logging.getLogger(__name__)


def _connection(db: Session, tenant_id: uuid.UUID, connection_id: uuid.UUID):
    bind_session_to_tenant(db, tenant_id)
    connection = sso_service.get_connection(db, tenant_id, connection_id)
    if connection is None or not connection.is_active or connection.protocol != SsoProtocol.SAML:
        raise WebNotFound()
    return connection


@router.get("/{tenant_id}/{connection_id}/metadata")
def metadata(request: Request, tenant_id: uuid.UUID, connection_id: uuid.UUID, db: Session = Depends(get_db)) -> Response:
    """What the bank gives its identity provider: entity ID, ACS and SLO URLs, and the certificate."""
    connection = _connection(db, tenant_id, connection_id)
    xml = saml_service.metadata_xml(connection, saml_service.base_url(str(request.base_url)))
    return Response(xml, media_type="application/samlmetadata+xml")


@router.post("/{tenant_id}/{connection_id}/acs")
def assertion_consumer(
    request: Request,
    tenant_id: uuid.UUID,
    connection_id: uuid.UUID,
    SAMLResponse: str = Form(...),  # noqa: N803 -- SAML's own field names
    RelayState: str = Form(""),  # noqa: N803
    db: Session = Depends(get_db),
) -> Response:
    connection = _connection(db, tenant_id, connection_id)

    def error_page(message: str) -> HTMLResponse:
        response = render_template(request, "auth/login.html", status_code=401, error=message)
        clear_saml_state_cookie(response)
        return response

    # Sign-in we started: the cookie says which request this must answer, and the
    # RelayState must be the one we sent. Without the cookie it can only be sign-in started
    # at the identity provider, which the connection must allow.
    request_id, next_path = None, None
    cookie = request.cookies.get(SAML_STATE_COOKIE_NAME)
    if cookie:
        try:
            state = decode_sso_state_token(cookie)
        except jwt.PyJWTError:
            return error_page("Your sign-in attempt expired — please try again.")
        if state["connection_id"] != str(connection.id) or state["tenant_id"] != str(tenant_id) or not secrets.compare_digest(
            state.get("state", ""), RelayState
        ):
            return error_page("Single sign-on state mismatch — please try again.")
        request_id, next_path = state["nonce"], state.get("next_path")
    try:
        assertion = saml_service.process_response(
            connection, saml_service.base_url(str(request.base_url)), {"SAMLResponse": SAMLResponse, "RelayState": RelayState},
            request_id=request_id,
        )
        sso_service.record_assertion(db, tenant_id, connection, assertion.assertion_id, assertion.expires_at)
    except (saml_service.SamlError, sso_service.Replayed) as exc:
        logger.warning("SAML sign-in failed for connection %s: %s", connection.id, exc)
        db.rollback()
        return error_page(_SSO_FAILED_MESSAGE)
    if request_id is None and RelayState.startswith("/"):
        next_path = RelayState  # an identity provider's target page; safe_next_path keeps it on this site
    claims = OidcClaims(email=assertion.email, subject=assertion.subject, groups=assertion.groups)
    return finish_sso_login(request, db, tenant_id, connection, claims, next_path, error_page)


@router.get("/{tenant_id}/{connection_id}/slo")
def single_logout(request: Request, tenant_id: uuid.UUID, connection_id: uuid.UUID, db: Session = Depends(get_db)) -> Response:
    """A signed LogoutRequest from the identity provider: end the person's OpenClear
    sessions, everywhere, and answer with a LogoutResponse."""
    connection = _connection(db, tenant_id, connection_id)
    try:
        name_id, redirect = saml_service.process_logout_request(
            connection, saml_service.base_url(str(request.base_url)), dict(request.query_params),
        )
    except saml_service.SamlError as exc:
        logger.warning("SAML logout failed for connection %s: %s", connection.id, exc)
        return Response("Logout request rejected.", status_code=400, media_type="text/plain")
    user = sso_service.find_identity_user(db, connection, name_id) if name_id else None
    if user is not None:
        session_service.revoke_all_sessions(db, user)
        audit_log_service.record_action(
            db, tenant_id, actor_user_id=user.id, channel="web", action="user.logout_sso",
            summary=f"Signed out by the identity provider ({connection.display_name})", resource_type="user", resource_id=user.id,
        )
        db.commit()
    return RedirectResponse(redirect or "/ui/login", status_code=303)
