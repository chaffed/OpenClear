# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

from pydantic import BaseModel, EmailStr


class LoginRequest(BaseModel):
    tenant_slug: str
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class LoginResponse(BaseModel):
    """Superset of TokenResponse: when the user has a registered WebAuthn credential,
    the password check alone isn't enough to log in — access_token/refresh_token are
    withheld and mfa_token is returned instead, to be exchanged via
    /auth/webauthn/login/options + /verify. mfa_token is useless for anything else: it
    carries no permissions (see auth/deps.py's get_mfa_pending_context)."""

    mfa_required: bool
    access_token: str | None = None
    refresh_token: str | None = None
    mfa_token: str | None = None
    token_type: str = "bearer"


class RefreshRequest(BaseModel):
    refresh_token: str
