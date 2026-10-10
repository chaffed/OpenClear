# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Continuous control monitoring (COMPLIANCE_PLAN.md C4; services/controls_service.py)."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from openclear.auth.deps import require_permission
from openclear.db.session import get_db
from openclear.db.tenancy import TenantContext
from openclear.services import controls_service

router = APIRouter(prefix="/controls", tags=["controls"])


@router.get("/status")
def control_status(db: Session = Depends(get_db), ctx: TenantContext = Depends(require_permission("audit_log:read"))) -> dict:
    """Each control's live status (`PASS`, `WARN` or `FAIL`) with its evidence, and `overall`
    (the worst of them): secrets and storage encryption, the action log's integrity,
    segregation of duties, admins' second factor, the anomaly-capture test, malformed-file
    handling, decision and import timeliness, integration secret age, production settings."""
    if ctx.customer_id is not None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Controls are for the bank's own staff.")
    return controls_service.summary(controls_service.status(db, ctx.tenant_id))
