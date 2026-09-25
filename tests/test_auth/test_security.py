# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import uuid

from openclear.auth.security import create_token, decode_token
from openclear.config import get_settings


def test_create_token_uses_global_default_when_no_override():
    settings = get_settings()
    token = create_token(
        user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), security_group_id=uuid.uuid4(), token_type="access"
    )
    claims = decode_token(token)
    expected_seconds = settings.jwt_access_token_expire_minutes * 60
    assert abs((claims["exp"] - claims["iat"]) - expected_seconds) < 2


def test_create_token_honors_per_tenant_access_override():
    token = create_token(
        user_id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        security_group_id=uuid.uuid4(),
        token_type="access",
        access_token_expire_minutes=5,
        refresh_token_expire_minutes=999,
    )
    claims = decode_token(token)
    assert abs((claims["exp"] - claims["iat"]) - 5 * 60) < 2


def test_create_token_honors_per_tenant_refresh_override():
    token = create_token(
        user_id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        security_group_id=uuid.uuid4(),
        token_type="refresh",
        access_token_expire_minutes=999,
        refresh_token_expire_minutes=7,
    )
    claims = decode_token(token)
    assert abs((claims["exp"] - claims["iat"]) - 7 * 60) < 2


def test_create_token_mfa_pending_ignores_overrides():
    settings = get_settings()
    token = create_token(
        user_id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        security_group_id=uuid.uuid4(),
        token_type="mfa_pending",
        access_token_expire_minutes=999,
        refresh_token_expire_minutes=999,
    )
    claims = decode_token(token)
    expected_seconds = settings.mfa_pending_token_expire_minutes * 60
    assert abs((claims["exp"] - claims["iat"]) - expected_seconds) < 2
