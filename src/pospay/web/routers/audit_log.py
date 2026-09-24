# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from pospay.db.session import get_db
from pospay.db.tenancy import TenantContext
from pospay.domain.audit_log_entry import AuditLogEntry
from pospay.repositories.audit_log_repo import AuditLogRepository
from pospay.repositories.user_repo import UserRepository
from pospay.services import audit_log_service
from pospay.web.deps import render_template, require_web_permission
from pospay.web.pagination import paginate

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
    return render_template(request, "audit_log/list.html", ctx=ctx, rows=rows, page_obj=page_obj)


@router.get("/verify")
def verify_audit_log(
    request: Request, page: int = 1, db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("audit_log:read")),
) -> HTMLResponse:
    result = audit_log_service.verify_chain(db, ctx.tenant_id)
    rows, page_obj = _rows_for_page(db, ctx.tenant_id, page)
    return render_template(request, "audit_log/list.html", ctx=ctx, rows=rows, page_obj=page_obj, verification=result)
