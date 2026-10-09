# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Decision files from the outbox (SDK_PLAN.md P3; services/decision_outbox_service.py)."""

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from openclear.api.v1.files import ANY_FILE
from openclear.api.v1.pagination import Page, page_params, set_page_headers
from openclear.auth.deps import require_permission
from openclear.db.session import get_db
from openclear.db.tenancy import TenantContext
from openclear.services import audit_log_service, decision_outbox_service

router = APIRouter(prefix="/decision-batches", tags=["decision-batches"])
_read = require_permission("exception:read")


def _bank_wide(ctx: TenantContext = Depends(_read)) -> TenantContext:
    if ctx.customer_id is not None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Decision files cover the whole bank; a customer-scoped caller can't use them")
    return ctx


class DecisionBatchRead(BaseModel):
    id: uuid.UUID
    layout: str
    from_sequence: int
    to_sequence: int
    decision_count: int
    filename: str
    media_type: str
    sha256: str
    size: int
    created_at: datetime | None
    acknowledged_at: datetime | None


class RunRequest(BaseModel):
    layout: str | None = None


def _read_model(batch) -> DecisionBatchRead:
    return DecisionBatchRead.model_validate(batch, from_attributes=True)


@router.get("", response_model=list[DecisionBatchRead])
def list_decision_batches(
    request: Request, response: Response, unacknowledged_only: bool = False, page: Page = Depends(page_params),
    db: Session = Depends(get_db), ctx: TenantContext = Depends(_bank_wide),
) -> list[DecisionBatchRead]:
    """Decision files the outbox wrote, oldest first. `unacknowledged_only=true` for the ones
    a core still has to process."""
    rows, total = decision_outbox_service.list_batches(db, ctx.tenant_id, unacknowledged_only=unacknowledged_only,
                                                       limit=page.limit, offset=page.offset)
    set_page_headers(request, response, page, total)
    return [_read_model(b) for b in rows]


@router.post("", response_model=DecisionBatchRead | None)
def run_outbox(payload: RunRequest | None = None, db: Session = Depends(get_db), ctx: TenantContext = Depends(_bank_wide)) -> DecisionBatchRead | None:
    """Writes a file now with the decisions since the last one (in the outbox's layout, or
    `layout`). Returns null if there's nothing new."""
    try:
        batch = decision_outbox_service.run(db, ctx.tenant_id, layout=(payload.layout if payload else None))
    except decision_outbox_service.OutboxError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from None
    db.commit()
    return _read_model(batch) if batch else None


def _batch(db: Session, ctx: TenantContext, batch_id: uuid.UUID):
    batch = decision_outbox_service.get_batch(db, ctx.tenant_id, batch_id)
    if batch is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Decision batch not found")
    return batch


@router.get("/{batch_id}", response_model=DecisionBatchRead)
def get_decision_batch(batch_id: uuid.UUID, db: Session = Depends(get_db), ctx: TenantContext = Depends(_bank_wide)) -> DecisionBatchRead:
    return _read_model(_batch(db, ctx, batch_id))


@router.get("/{batch_id}/file", **ANY_FILE)
def get_decision_batch_file(batch_id: uuid.UUID, db: Session = Depends(get_db), ctx: TenantContext = Depends(_bank_wide)) -> Response:
    """The file, byte for byte as written; `X-Content-SHA256` lets the core check it."""
    from openclear import storage

    batch = _batch(db, ctx, batch_id)
    return Response(storage.read(batch.storage_ref), media_type=batch.media_type, headers={
        "Content-Disposition": f'attachment; filename="{batch.filename}"', "X-Content-SHA256": batch.sha256,
    })


@router.post("/{batch_id}/acknowledge", response_model=DecisionBatchRead)
def acknowledge_decision_batch(batch_id: uuid.UUID, db: Session = Depends(get_db), ctx: TenantContext = Depends(_bank_wide)) -> DecisionBatchRead:
    """Confirms the core processed this file. Repeating it is harmless."""
    batch = _batch(db, ctx, batch_id)
    first_time = batch.acknowledged_at is None
    decision_outbox_service.acknowledge(db, ctx.tenant_id, batch, user_id=ctx.user_id)
    if first_time:
        audit_log_service.record_action(
            db, ctx.tenant_id, actor_user_id=ctx.user_id, channel="api", action="decision_batch.acknowledge",
            summary=f"Acknowledged decision file {batch.filename}", resource_type="decision_batch", resource_id=batch.id,
        )
    db.commit()
    return _read_model(batch)
