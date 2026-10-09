# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""OAuth 2.0 token endpoint for a bank's systems (SDK_PLAN.md P0): the client credentials
grant (RFC 6749 section 4.4). See services/api_client_service.py."""

import base64
import binascii
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from openclear.auth.security import create_token
from openclear.config import get_settings
from openclear.db.session import get_db
from openclear.domain.tenant_membership import TenantMembership
from openclear.domain.user import User
from openclear.services import api_client_service
from openclear.web.rate_limit import rate_limit

router = APIRouter(prefix="/oauth", tags=["oauth"])

_NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache"}


def _error(code: str, description: str, status_code: int) -> JSONResponse:
    headers = dict(_NO_STORE)
    if status_code == 401:
        headers["WWW-Authenticate"] = 'Basic realm="openclear"'
    return JSONResponse({"error": code, "error_description": description}, status_code=status_code, headers=headers)


def _basic_credentials(request: Request) -> tuple[str, str] | None:
    header = request.headers.get("authorization", "")
    if not header.lower().startswith("basic "):
        return None
    try:
        decoded = base64.b64decode(header[6:].strip(), validate=True).decode()
    except (binascii.Error, UnicodeDecodeError):
        return None
    client_id, _, secret = decoded.partition(":")
    return client_id, secret


@router.post("/token")
def token(
    request: Request,
    grant_type: str = Form(...),
    client_id: str | None = Form(None),
    client_secret: str | None = Form(None),
    db: Session = Depends(get_db),
    _rate_limit: None = Depends(rate_limit("oauth_token", limit_setting="oauth_token_rate_limit_per_minute")),
) -> JSONResponse:
    """`grant_type=client_credentials`, with the client's id and secret either in HTTP Basic
    auth or as `client_id` and `client_secret` form fields. Returns a bearer access token
    for every `/api/v1` endpoint the client's security group allows."""
    if grant_type != "client_credentials":
        return _error("unsupported_grant_type", "Only client_credentials is supported.", 400)
    basic = _basic_credentials(request)
    if basic is not None:
        client_id, client_secret = basic
    client = api_client_service.authenticate(db, client_id or "", client_secret or "")
    if client is None:
        db.rollback()
        return _error("invalid_client", "Unknown client, wrong secret, or the client has been revoked.", 401)

    membership = db.get(TenantMembership, client.membership_id)
    user = db.get(User, client.user_id)
    minutes = get_settings().api_client_token_minutes
    access_token = create_token(
        user_id=user.id, tenant_id=client.tenant_id, security_group_id=membership.security_group_id, token_type="access",
        customer_id=membership.customer_id, token_version=user.token_version,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=minutes),
    )
    db.commit()  # last_used_at
    return JSONResponse(
        {"access_token": access_token, "token_type": "Bearer", "expires_in": minutes * 60}, headers=_NO_STORE,
    )
