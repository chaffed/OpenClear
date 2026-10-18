# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import uuid

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from openclear.auth.deps import RECENT_SIGN_IN_REQUIRED, get_current_context, get_mfa_pending_context, recently_signed_in
from openclear.auth.security import create_session_tokens
from openclear.auth.webauthn_service import (
    WebauthnError,
    begin_authentication,
    begin_registration,
    complete_authentication,
    complete_registration,
    delete_credential,
    list_credentials,
    record_key_change,
)
from openclear.db.session import get_db
from openclear.db.tenancy import TenantContext
from openclear.domain.user import User
from openclear.schemas.auth import TokenResponse
from openclear.schemas.webauthn import AuthenticationVerifyRequest, RegistrationVerifyRequest, WebauthnCredentialRead
from openclear.services import user_service

router = APIRouter(prefix="/auth/webauthn", tags=["webauthn"])


def _get_user_or_500(db: Session, ctx: TenantContext) -> User:
    user = db.get(User, ctx.user_id)
    if user is None:
        # Shouldn't happen: a valid token was issued for this user_id, so a missing row
        # here means the user was deleted after the token was issued, not a client error.
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User no longer exists")
    return user


def _require_recent_sign_in(ctx: TenantContext) -> None:
    """Adding or removing a key needs a recent sign-in (auth/deps.py::recently_signed_in)."""
    if not recently_signed_in(ctx):
        raise HTTPException(status.HTTP_403_FORBIDDEN, RECENT_SIGN_IN_REQUIRED)


@router.post("/register/options")
def register_options(
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(get_current_context),
) -> Response:
    _require_recent_sign_in(ctx)
    user = _get_user_or_500(db, ctx)
    options_json = begin_registration(db, user, ctx.tenant_id)
    db.commit()
    return Response(content=options_json, media_type="application/json")


@router.post("/register/verify", response_model=WebauthnCredentialRead, status_code=status.HTTP_201_CREATED)
def register_verify(
    payload: RegistrationVerifyRequest,
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(get_current_context),
) -> WebauthnCredentialRead:
    _require_recent_sign_in(ctx)
    user = _get_user_or_500(db, ctx)
    try:
        credential = complete_registration(db, user, ctx.tenant_id, payload.credential, nickname=payload.nickname)
    except WebauthnError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from None
    record_key_change(db, ctx, user, added=True, nickname=credential.nickname, channel="api")
    db.commit()
    return WebauthnCredentialRead.model_validate(credential)


@router.get("/credentials", response_model=list[WebauthnCredentialRead])
def get_credentials(
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(get_current_context),
) -> list[WebauthnCredentialRead]:
    credentials = list_credentials(db, ctx.tenant_id, ctx.user_id)
    return [WebauthnCredentialRead.model_validate(c) for c in credentials]


@router.delete("/credentials/{credential_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_credential(
    credential_id: uuid.UUID,
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(get_current_context),
) -> None:
    _require_recent_sign_in(ctx)
    nickname = next((c.nickname for c in list_credentials(db, ctx.tenant_id, ctx.user_id) if c.id == credential_id), None)
    deleted = delete_credential(db, ctx.tenant_id, ctx.user_id, credential_id)
    if not deleted:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Credential not found")
    record_key_change(db, ctx, _get_user_or_500(db, ctx), added=False, nickname=nickname, channel="api")
    db.commit()


@router.post("/login/options")
def login_options(
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(get_mfa_pending_context),
) -> Response:
    user = _get_user_or_500(db, ctx)
    try:
        options_json = begin_authentication(db, user, ctx.tenant_id)
    except WebauthnError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from None
    db.commit()
    return Response(content=options_json, media_type="application/json")


@router.post("/login/verify", response_model=TokenResponse)
def login_verify(
    payload: AuthenticationVerifyRequest,
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(get_mfa_pending_context),
) -> TokenResponse:
    user = _get_user_or_500(db, ctx)
    try:
        complete_authentication(db, user, ctx.tenant_id, payload.credential)
    except WebauthnError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from None

    # customer_id is carried from the mfa_pending token: it was previously omitted here,
    # so a customer-scoped user completing WebAuthn over the API got a token for the
    # wrong (tenant-wide) membership, or none at all.
    session_tokens = create_session_tokens(
        user_id=user.id,
        tenant_id=ctx.tenant_id,
        security_group_id=ctx.security_group_id,
        customer_id=ctx.customer_id,
        token_version=user.token_version,
        access_token_expire_minutes=ctx.access_token_expire_minutes,
        refresh_token_expire_minutes=ctx.refresh_token_expire_minutes,
        signed_in_with=ctx.signed_in_with,
    )
    tokens = TokenResponse(access_token=session_tokens.access_token, refresh_token=session_tokens.refresh_token)
    user_service.record_login(db, user.id, tenant_id=ctx.tenant_id)
    db.commit()
    return tokens
