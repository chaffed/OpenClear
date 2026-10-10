# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Pull feeds for a bank's SIEM or archive (SIEM_PLAN.md S4): its security events, and its
action log with each entry's signature. Both are read with a cursor, oldest first, like the
decision feed."""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from openclear.auth.deps import require_permission
from openclear.auth.platform_api_key_deps import require_platform_scope
from openclear.db.session import get_db
from openclear.db.tenancy import TenantContext
from openclear.domain.audit_log_entry import AuditLogEntry
from openclear.domain.tenant import Tenant
from openclear.services import audit_log_service, security_events

router = APIRouter(tags=["security events"])
_read = require_permission("audit_log:read")


def _bank_staff(ctx: TenantContext) -> None:
    if ctx.customer_id is not None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "These feeds are for the bank's own staff.")


@router.get("/security-events")
def list_security_events(
    after: int = Query(0, ge=0, description="The `sequence` of the last event already read (0 for the start)"),
    limit: int = Query(100, ge=1, le=1000), db: Session = Depends(get_db), ctx: TenantContext = Depends(_read),
) -> dict:
    """This bank's security events after `after`, oldest first: sign-ins and their failures,
    lockouts, second factors, tokens, refusals, and changes to who may do what. Pass the last
    `sequence` back as `after` to continue; `next_after` is the one to use next. Events are
    kept for `security_event_retention_days` (30 by default)."""
    _bank_staff(ctx)
    events = security_events.feed(db, ctx.tenant_id, after=after, limit=limit)
    return {"events": [security_events.as_record(e, ctx.tenant_slug) for e in events],
            "next_after": events[-1].id if events else after}


@router.get("/audit-log")
def list_audit_log(
    after_sequence: int = Query(0, ge=0, description="The `sequence` of the last entry already read (0 for the start)"),
    limit: int = Query(100, ge=1, le=1000), db: Session = Depends(get_db), ctx: TenantContext = Depends(_read),
) -> dict:
    """This bank's action log after `after_sequence`, oldest first, each entry with what
    it chains onto (`prev_entry_hash`), its signature (`entry_hash`: ECDSA P-256 over SHA-256)
    and the exact text that was signed (`signed_text`). With `public_key` (the action-log
    key), anyone can check each entry, and that each one chains onto the one before, without
    trusting OpenClear."""
    _bank_staff(ctx)
    entries = list(db.execute(select(AuditLogEntry).where(
        AuditLogEntry.tenant_id == ctx.tenant_id, AuditLogEntry.sequence > after_sequence,
    ).order_by(AuditLogEntry.sequence).limit(limit)).scalars())
    return {
        "entries": [{
            "sequence": e.sequence, "occurred_at": e.occurred_at.isoformat(), "actor_user_id": str(e.actor_user_id) if e.actor_user_id else None,
            "channel": e.channel.value, "action": e.action, "summary": e.summary, "resource_type": e.resource_type,
            "resource_id": str(e.resource_id) if e.resource_id else None, "metadata": e.metadata_json,
            "prev_entry_hash": e.prev_entry_hash, "entry_hash": e.entry_hash, "signed_text": audit_log_service.signed_text(e),
        } for e in entries],
        "next_after_sequence": entries[-1].sequence if entries else after_sequence,
        "signature": {"algorithm": "ECDSA-P256-SHA256", "encoding": "hex DER", "public_key": audit_log_service.audit_public_key_pem()},
    }


@router.get("/platform/security-events", tags=["platform"])
def platform_security_events(
    after: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=1000), db: Session = Depends(get_db),
    _key=Depends(require_platform_scope("operations")),
) -> dict:
    """Every bank's security events, and those that belong to no bank (such as a failed
    sign-in naming a bank that doesn't exist), for the installation's operator."""
    events = security_events.feed(db, None, after=after, limit=limit, all_banks=True)
    slugs = dict(db.execute(select(Tenant.id, Tenant.slug)).all()) if events else {}
    return {"events": [security_events.as_record(e, slugs.get(e.tenant_id)) for e in events],
            "next_after": events[-1].id if events else after}
