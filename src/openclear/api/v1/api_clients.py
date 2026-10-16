# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""API clients, for access reviews (SDK_PLAN.md P0): the bank's, and a self-managing customer's
own (MOBILE_PLAN.md R4). Created, rotated and revoked in the web UI (Settings → API clients),
where a new secret can be shown once."""

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from openclear.auth.deps import get_current_context
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
    managed_by: str = "bank"  # "customer": one a self-managing customer made for its own systems
    accounts: str | None = None  # a customer's client: all its accounts, or which
    can_act_for_users: bool
    read_only: bool = False
    created_by: str | None
    created_at: datetime | None
    secret_rotated_at: datetime | None
    last_used_at: datetime | None
    revoked_at: datetime | None


@router.get("", response_model=list[ApiClientRead])
def list_api_clients(db: Session = Depends(get_db), ctx: TenantContext = Depends(get_current_context)) -> list[ApiClientRead]:
    """Every API client of the bank (`api_client:manage`), customers' own marked `managed_by:
    customer`; or, for a customer's own session (`customer_api_client:manage`), the clients that
    customer made."""
    if ctx.customer_id is not None and "customer_api_client:manage" in ctx.permissions:
        clients = api_client_service.list_customer_clients(db, ctx.tenant_id, ctx.customer_id)
    elif ctx.customer_id is None and "api_client:manage" in ctx.permissions:
        clients = api_client_service.list_clients(db, ctx.tenant_id)
    else:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Missing permission: api_client:manage")
    return [ApiClientRead(**api_client_service.describe(db, c)) for c in clients]
