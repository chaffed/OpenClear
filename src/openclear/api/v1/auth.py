# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from openclear.auth.login_service import PasswordLoginOutcome, authenticate_password
from openclear.auth.security import create_session_tokens, create_token
from openclear.db.session import get_db
from openclear.schemas.auth import LoginRequest, LoginResponse, RefreshRequest, TokenResponse
from openclear.services import demo_tenant_service, session_service, user_service

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=LoginResponse)
def login(payload: LoginRequest, db: Session = Depends(get_db)) -> LoginResponse:
    # See web/routers/auth.py::login_submit's identical call for why this runs before
    # any identity for this request is resolved -- a no-op for every non-demo tenant.
    demo_tenant_service.maybe_reset_if_demo_idle_by_slug(db, payload.tenant_slug)
    db.commit()
    result = authenticate_password(db, payload.tenant_slug, payload.email, payload.password)
    # authenticate_password only flushes (see its docstring) — commit unconditionally,
    # even on a failed attempt, so the incremented failed_login_attempts/locked_until
    # actually persist rather than being discarded when this request's session closes.
    db.commit()
    if result.outcome == PasswordLoginOutcome.SSO_REQUIRED:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "This organization requires single sign-on")
    if result.outcome == PasswordLoginOutcome.PENDING_APPROVAL:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Waiting for the bank to approve this person")
    if result.outcome != PasswordLoginOutcome.SUCCESS or result.identity is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid credentials")
    identity = result.identity
    user, tenant, membership = identity.user, identity.tenant, identity.membership

    if identity.mfa_required:
        mfa_token = create_token(
            user_id=user.id,
            tenant_id=tenant.id,
            security_group_id=membership.security_group_id,
            customer_id=membership.customer_id,
            token_type="mfa_pending",
            token_version=user.token_version,
            signed_in_with="password",
        )
        return LoginResponse(mfa_required=True, mfa_token=mfa_token)

    tokens = create_session_tokens(
        user_id=user.id,
        tenant_id=tenant.id,
        security_group_id=membership.security_group_id,
        customer_id=membership.customer_id,
        token_version=user.token_version,
        access_token_expire_minutes=tenant.access_token_expire_minutes,
        refresh_token_expire_minutes=tenant.refresh_token_expire_minutes,
        signed_in_with="password",
    )
    response = LoginResponse(mfa_required=False, access_token=tokens.access_token, refresh_token=tokens.refresh_token)
    user_service.record_login(db, user.id, tenant_id=membership.tenant_id)
    db.commit()
    return response


@router.post("/refresh", response_model=TokenResponse)
def refresh(payload: RefreshRequest, db: Session = Depends(get_db)) -> TokenResponse:
    """Continues the session: a new access token (idle timeout) and refresh token, never
    past the session's original maximum length. Rejects a session that was logged out or
    signed out everywhere — see services/session_service.py::refresh_session, shared with
    the web UI's own refresh."""
    try:
        tokens, _tenant = session_service.refresh_session(db, payload.refresh_token)
    except session_service.RefreshFailed as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(exc)) from None
    return TokenResponse(access_token=tokens.access_token, refresh_token=tokens.refresh_token)
