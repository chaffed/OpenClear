# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""The integration checklist (SDK_PLAN.md P4; services/conformance_service.py)."""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from openclear.auth.deps import require_permission
from openclear.db.session import get_db
from openclear.db.tenancy import TenantContext
from openclear.services import conformance_service

router = APIRouter(prefix="/conformance", tags=["conformance"])
_read = require_permission("exception:read")


@router.get("")
def integration_checklist(db: Session = Depends(get_db), ctx: TenantContext = Depends(_read)) -> dict:
    """What this bank's integration has shown it does: each check `pass`, `todo` or `fail`,
    and `certified` once every required one passes (docs/guides/certification.md)."""
    return conformance_service.summary(conformance_service.checklist(db, ctx.tenant_id))


@router.post("/webhook-probe")
def webhook_probe(db: Session = Depends(get_db), ctx: TenantContext = Depends(require_permission("api_client:manage"))) -> dict:
    """Sends the first webhook endpoint a signed test event (it should accept it) and a
    forged one (it must reject it), and records the result."""
    result = conformance_service.probe_webhooks(db, ctx.tenant_id)
    db.commit()
    return {"passed": result.passed, "detail": result.detail}
