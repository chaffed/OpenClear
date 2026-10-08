# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import uuid

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, Response, StreamingResponse
from sqlalchemy.orm import Session

from openclear.db.session import get_db
from openclear.db.tenancy import TenantContext
from openclear.domain.audit_log_entry import AuditLogEntry
from openclear.repositories.audit_log_repo import AuditLogRepository
from openclear.repositories.user_repo import UserRepository
from openclear import storage
from openclear.services import audit_archive_service, audit_log_service
from openclear.web.deps import WebNotFound, render_template, require_web_permission
from openclear.web.pagination import paginate

router = APIRouter(prefix="/ui/audit-log", tags=["web-audit-log"])


def _rows_for_page(db: Session, tenant_id, page: int):
    page_obj = paginate(
        page=page,
        count_fn=lambda: AuditLogRepository(db, tenant_id).count(),
        list_fn=lambda **kw: audit_log_service.list_entries(db, tenant_id, order_by=AuditLogEntry.sequence.desc(), **kw),
    )
    user_repo = UserRepository(db)
    rows = []
    for entry in page_obj.items:
        actor = user_repo.get(entry.actor_user_id) if entry.actor_user_id else None
        rows.append({"entry": entry, "actor_email": actor.email if actor else "(system)"})
    return rows, page_obj


@router.get("")
def list_audit_log(
    request: Request, page: int = 1, db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("audit_log:read")),
) -> HTMLResponse:
    rows, page_obj = _rows_for_page(db, ctx.tenant_id, page)
    return render_template(
        request, "audit_log/list.html", ctx=ctx, rows=rows, page_obj=page_obj,
        checkpoints=audit_archive_service.list_checkpoints(db, ctx.tenant_id),
    )


@router.get("/verify")
def verify_audit_log(
    request: Request, page: int = 1, db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("audit_log:read")),
) -> HTMLResponse:
    result = audit_log_service.verify_chain(db, ctx.tenant_id)
    checkpoints = audit_archive_service.list_checkpoints(db, ctx.tenant_id)
    archives = audit_archive_service.verify_archives(db, ctx.tenant_id) if checkpoints else None
    rows, page_obj = _rows_for_page(db, ctx.tenant_id, page)
    return render_template(
        request, "audit_log/list.html", ctx=ctx, rows=rows, page_obj=page_obj, verification=result,
        checkpoints=checkpoints, archive_check=archives,
    )


@router.get("/archives/{checkpoint_id}")
def download_audit_archive(
    checkpoint_id: uuid.UUID, db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("audit_log:read")),
) -> Response:
    """An archive of older audit entries: gzipped JSON Lines, one entry per line with
    every field needed to re-verify its signature."""
    checkpoint = next((c for c in audit_archive_service.list_checkpoints(db, ctx.tenant_id) if c.id == checkpoint_id), None)
    if checkpoint is None:
        raise WebNotFound()
    filename = f"audit-log-{checkpoint.first_sequence}-{checkpoint.through_sequence}.jsonl.gz"
    return StreamingResponse(
        storage.iter_chunks(checkpoint.archive_ref), media_type="application/gzip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
