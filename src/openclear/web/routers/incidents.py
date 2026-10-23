# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""A bank's administrator acknowledges the service provider's incident notice
(services/incident_service.py; REGULATORY_PLAN.md RG3): the banner on every page goes, and the
acknowledgement is recorded as evidence of when the bank learned."""

import uuid

from fastapi import APIRouter, Depends, Form
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from openclear.db.session import get_db
from openclear.db.tenancy import TenantContext
from openclear.services import incident_service
from openclear.web.deps import WebNotFound, require_any_web_permission
from openclear.web.redirects import see_other
from openclear.web.security import safe_next_path, verify_csrf

router = APIRouter(prefix="/ui/incident-notices", tags=["web-incidents"])


@router.post("/{delivery_id}/acknowledge")
def acknowledge(delivery_id: uuid.UUID, next: str = Form("/ui/"), db: Session = Depends(get_db),
                ctx: TenantContext = Depends(require_any_web_permission("admin:manage", "tenant:manage")),
                _csrf: None = Depends(verify_csrf)) -> RedirectResponse:
    if ctx.customer_id is not None:
        raise WebNotFound()
    try:
        incident_service.acknowledge(db, ctx.tenant_id, delivery_id, user_id=ctx.user_id)
    except incident_service.IncidentError:
        raise WebNotFound() from None
    db.commit()
    return see_other(safe_next_path(next, default="/ui/"), flash="Notice acknowledged.")
