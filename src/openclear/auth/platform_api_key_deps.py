# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Auth dependency for the usage-metering API only (api/v1/platform_usage.py) --
completely separate from every other route's tenant-scoped JWT bearer auth
(auth/deps.py). A distinct header (X-Api-Key, not Authorization: Bearer) keeps the two
mechanisms from ever being confused with each other at the routing level."""

from fastapi import Depends, HTTPException, status
from fastapi.security import APIKeyHeader
from sqlalchemy.orm import Session

from openclear.db.session import get_db
from openclear.domain.platform_api_key import PlatformApiKey
from openclear.services import platform_api_key_service

_api_key_header = APIKeyHeader(name="X-Api-Key", auto_error=True)


def require_platform_scope(scope: str):
    """A valid, unrevoked platform key that carries `scope` (PlatformApiKey.scopes) —
    401 for a bad key, 403 for a good key without the scope, so a usage-metering key
    can never operate the shared model."""

    def _check(api_key: str = Depends(_api_key_header), db: Session = Depends(get_db)) -> PlatformApiKey:
        key = platform_api_key_service.verify(db, api_key)
        if key is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or revoked API key")
        db.commit()
        if scope not in (key.scopes or []):
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"This API key doesn't have the {scope!r} scope")
        return key

    return _check


# The usage-metering API's dependency (api/v1/platform_usage.py). Every key created
# before scopes existed has "usage", so existing integrations keep working.
require_platform_api_key = require_platform_scope("usage")
