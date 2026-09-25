# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class RegistrationVerifyRequest(BaseModel):
    credential: dict[str, Any]  # raw navigator.credentials.create() response, JSON-serialized by the browser
    nickname: str | None = None


class AuthenticationVerifyRequest(BaseModel):
    # mfa_token is NOT a body field here — it's passed the same way an access token is,
    # via "Authorization: Bearer <mfa_token>" (see auth.get_mfa_pending_context), so this
    # endpoint's auth mechanism is consistent with every other authenticated endpoint.
    credential: dict[str, Any]  # raw navigator.credentials.get() response, JSON-serialized by the browser


class WebauthnCredentialRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    nickname: str | None
    aaguid: str | None
    created_at: datetime
    last_used_at: datetime | None
