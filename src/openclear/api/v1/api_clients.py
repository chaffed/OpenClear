# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""The bank's API clients, for access reviews (SDK_PLAN.md P0). Created, rotated and
revoked in the web UI (Settings → API clients), where a new secret can be shown once."""

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from openclear.auth.deps import require_permission
from openclear.db.session import get_db
from openclear.db.tenancy import TenantContext
from openclear.services import api_client_service

router = APIRouter(prefix="/api-clients", tags=["api-clients"])


class ApiClientRead(BaseModel):
    id: uuid.UUID
    name: str
    client_id: str
    security_group: str | None
    customer: str | None
    can_act_for_users: bool
    created_by: str | None
    created_at: datetime | None
    secret_rotated_at: datetime | None
    last_used_at: datetime | None
    revoked_at: datetime | None


@router.get("", response_model=list[ApiClientRead])
def list_api_clients(db: Session = Depends(get_db), ctx: TenantContext = Depends(require_permission("api_client:manage"))) -> list[ApiClientRead]:
    return [ApiClientRead(**api_client_service.describe(db, c)) for c in api_client_service.list_clients(db, ctx.tenant_id)]
