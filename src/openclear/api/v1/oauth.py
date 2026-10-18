# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""OAuth 2.0 token endpoint for a bank's systems (SDK_PLAN.md P0): the client credentials
grant (RFC 6749 section 4.4), and token exchange (RFC 8693, phase F) for an online banking
provider acting for the person signed in to it. See services/api_client_service.py."""

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


def _key_pairs_only(db: Session, client) -> bool:
    """The bank requires API clients to authenticate with a signed assertion (OWASP_PLAN.md OW-D26)."""
    from openclear.domain.tenant import Tenant

    tenant = db.get(Tenant, client.tenant_id)
    return bool(tenant and tenant.api_clients_key_pair_only)


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


def _token_endpoint_urls(request: Request) -> list[str]:
    """What a client assertion's `aud` may name: this endpoint's public address, and the
    address the request arrived at."""
    urls = [str(request.url).split("?", 1)[0]]
    base = get_settings().public_base_url
    if base:
        urls.append(base.rstrip("/") + "/api/v1/oauth/token")
    return urls


TOKEN_EXCHANGE = "urn:ietf:params:oauth:grant-type:token-exchange"
ID_TOKEN_TYPE = "urn:ietf:params:oauth:token-type:id_token"
ACCESS_TOKEN_TYPE = "urn:ietf:params:oauth:token-type:access_token"


@router.post("/token")
def token(
    request: Request,
    grant_type: str = Form(...),
    client_id: str | None = Form(None),
    client_secret: str | None = Form(None),
    subject_token: str | None = Form(None),
    subject_token_type: str | None = Form(None),
    requested_token_type: str | None = Form(None),
    client_assertion_type: str | None = Form(None),
    client_assertion: str | None = Form(None),
    db: Session = Depends(get_db),
    _rate_limit: None = Depends(rate_limit("oauth_token", limit_setting="oauth_token_rate_limit_per_minute")),
) -> JSONResponse:
    """`grant_type=client_credentials`, with the client's id and secret either in HTTP Basic
    auth or as `client_id` and `client_secret` form fields; or, for a client that signs in
    with a key pair, `client_assertion_type=urn:ietf:params:oauth:client-assertion-type:jwt-bearer`
    and `client_assertion`: a JWT signed with its private key (`iss` and `sub` the client id,
    `aud` this endpoint's URL, `exp` within five minutes, a unique `jti`). Returns a bearer access token
    for every `/api/v1` endpoint the client's security group allows.

    `grant_type=urn:ietf:params:oauth:grant-type:token-exchange` (RFC 8693) trades the ID
    token of a person signed in to the bank's online banking provider (`subject_token`,
    `subject_token_type=urn:ietf:params:oauth:token-type:id_token`) for a token that acts
    for them: what both they and the client may do, recorded as theirs. The client must be
    allowed to act for users and linked to that provider's SSO connection; the ID token is
    verified against the provider's keys, and the person is matched (or provisioned) with
    the connection's group mappings and customer claim, as at sign-in."""
    if grant_type not in ("client_credentials", TOKEN_EXCHANGE):
        return _error("unsupported_grant_type", "Supported: client_credentials and token exchange.", 400)
    if client_assertion_type is not None or client_assertion is not None:
        # A key-pair client (private_key_jwt, RFC 7523): a signed assertion instead of a secret.
        from openclear.auth import client_assertion as assertions

        if client_assertion_type != assertions.ASSERTION_TYPE or not client_assertion:
            return _error("invalid_request", f"Send client_assertion with client_assertion_type={assertions.ASSERTION_TYPE}.", 400)
        client = assertions.verify(db, client_assertion, audiences=_token_endpoint_urls(request))
        client_id = client.client_id if client is not None else client_id
    else:
        basic = _basic_credentials(request)
        if basic is not None:
            client_id, client_secret = basic
        client = api_client_service.authenticate(db, client_id or "", client_secret or "")
        if client is not None and _key_pairs_only(db, client):
            db.rollback()
            return _error("invalid_client", "This bank requires API clients to sign with a key pair (private_key_jwt), not a secret.", 401)
    if client is None:
        db.rollback()
        # Sampled per client id and source, so a flood of bad secrets is one event a minute.
        from openclear.services import security_events
        from openclear.web.client_ip import get_client_ip

        security_events.emit_detached(
            "auth.token.failed", sampled_key=f"{(client_id or '')[:100]}|{get_client_ip(request)}", outcome="failure",
            reason="invalid_client", actor_type="api_client", actor_label=(client_id or "")[:100] or None,
        )
        return _error("invalid_client", "Unknown client, wrong secret, or the client has been revoked.", 401)
    if grant_type == TOKEN_EXCHANGE:
        response = _exchange(db, client, subject_token, subject_token_type, requested_token_type)
        if response.status_code >= 400:
            import json

            from openclear.services import security_events

            security_events.emit_detached(
                "auth.token.failed", tenant_id=client.tenant_id, outcome="failure",
                reason=json.loads(response.body).get("error", "refused"), actor_type="api_client", actor_id=client.id,
                actor_label=client.name, details={"grant_type": "token_exchange"},
            )
        return response

    membership = db.get(TenantMembership, client.membership_id)
    user = db.get(User, client.user_id)
    minutes = get_settings().api_client_token_minutes
    access_token = create_token(
        user_id=user.id, tenant_id=client.tenant_id, security_group_id=membership.security_group_id, token_type="access",
        customer_id=membership.customer_id, token_version=user.token_version,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=minutes),
    )
    from openclear.services import security_events

    security_events.emit(db, "auth.token.issued", tenant_id=client.tenant_id, actor_type="api_client", actor_id=client.id,
                         actor_label=client.name, details={"grant_type": "client_credentials", "expires_in": minutes * 60})
    db.commit()  # last_used_at
    return JSONResponse(
        {"access_token": access_token, "token_type": "Bearer", "expires_in": minutes * 60}, headers=_NO_STORE,
    )


def _exchange(db: Session, client, subject_token: str | None, subject_token_type: str | None, requested_token_type: str | None) -> JSONResponse:
    from openclear.auth.oidc_service import OidcError, claims_to_identity, verify_provider_token
    from openclear.domain.sso_connection import SsoProtocol
    from openclear.services import audit_log_service, sso_service

    if not subject_token or subject_token_type != ID_TOKEN_TYPE:
        return _error("invalid_request", f"Send subject_token with subject_token_type={ID_TOKEN_TYPE}.", 400)
    if requested_token_type not in (None, "", ACCESS_TOKEN_TYPE):
        return _error("invalid_request", "Only access tokens are issued.", 400)
    connection = sso_service.get_connection(db, client.tenant_id, client.sso_connection_id) if client.sso_connection_id else None
    if not client.can_act_for_users or connection is None or not connection.is_active or connection.protocol != SsoProtocol.OIDC:
        return _error("unauthorized_client", "This client may not exchange tokens: it must be allowed to act for users and linked to an OIDC SSO connection.", 400)
    try:
        claims = verify_provider_token(connection, subject_token, kind="subject_token", require_exp=True)
        identity = claims_to_identity(connection, claims)
    except OidcError:
        return _error("invalid_grant", "The subject token was rejected.", 400)
    result = sso_service.complete_sso_login(db, client.tenant_id, connection, identity)
    if result.outcome != "success":
        db.commit()  # a membership the provider's groups no longer justify stays revoked
        return _error("invalid_grant", "That person has no access to OpenClear.", 400)
    client_membership = db.get(TenantMembership, client.membership_id)
    if client_membership.customer_id is not None and result.membership.customer_id != client_membership.customer_id:
        return _error("invalid_grant", "That person belongs to another customer than this client.", 400)

    service_user = db.get(User, client.user_id)
    now = datetime.now(timezone.utc)
    expires_at = min(now + timedelta(minutes=get_settings().api_client_token_minutes),
                     datetime.fromtimestamp(float(claims["exp"]), timezone.utc))
    access_token = create_token(
        user_id=service_user.id, tenant_id=client.tenant_id, security_group_id=client_membership.security_group_id,
        token_type="access", customer_id=client_membership.customer_id, token_version=service_user.token_version,
        expires_at=expires_at, on_behalf_of={"uid": str(result.user.id), "mid": str(result.membership.id)},
    )
    audit_log_service.record_action(
        db, client.tenant_id, actor_user_id=client.user_id, channel="api", action="api_client.token_exchange",
        summary=f"{client.name} exchanged {result.user.email}'s sign-in at {connection.display_name} for a token acting for them",
        resource_type="user", resource_id=result.user.id,
    )
    db.commit()
    return JSONResponse({
        "access_token": access_token, "issued_token_type": ACCESS_TOKEN_TYPE, "token_type": "Bearer",
        "expires_in": max(1, int((expires_at - now).total_seconds())),
    }, headers=_NO_STORE)
