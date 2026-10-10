# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import uuid

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from openclear.auth.permissions import PERMISSION_CATALOG
from openclear.auth import sod
from openclear.db.session import get_db
from openclear.db.tenancy import TenantContext
from openclear.domain.security_group import SecurityGroup
from openclear.repositories.security_group_repo import SecurityGroupRepository
from openclear.services import audit_log_service, security_group_service
from openclear.web.deps import WebNotFound, render_template, require_web_permission
from openclear.web.pagination import paginate
from openclear.web.security import verify_csrf

router = APIRouter(prefix="/ui/security-groups", tags=["web-security-groups"])


@router.get("")
def list_security_groups(
    request: Request, page: int = 1, db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("security_group:manage")),
) -> HTMLResponse:
    page_obj = paginate(
        page=page,
        count_fn=lambda: SecurityGroupRepository(db, ctx.tenant_id).count(),
        list_fn=lambda **kw: security_group_service.list_security_groups(
            db, ctx.tenant_id, order_by=SecurityGroup.created_at.desc(), **kw
        ),
    )
    return render_template(
        request, "security_groups/list.html", ctx=ctx, page_obj=page_obj, catalog_size=len(PERMISSION_CATALOG)
    )


def _form_with_error(request: Request, ctx: TenantContext, group, name: str, permissions: list[str], error: str | None = None,
                     status_code: int = 400) -> HTMLResponse:
    """Re-shows the form with what was typed when the name is already taken (the
    database's uniqueness constraint on (tenant, name)), or the group breaks segregation of
    duties (auth/sod.py)."""
    return render_template(
        request, "security_groups/form.html", ctx=ctx, catalog=PERMISSION_CATALOG, group=group,
        selected=set(permissions), typed_name=name, status_code=status_code,
        error=error or "A security group with that name already exists. Choose a different name.",
    )


def _sod_refusal(request: Request, db: Session, ctx: TenantContext, group, name: str, permissions: list[str]) -> HTMLResponse | None:
    """Segregation of duties: refused in enforce mode (and logged); allowed with a logged
    warning in warn mode."""
    try:
        sod.check_group(db, ctx.tenant_id, ctx.user_id, permissions, group_name=name)
    except sod.SodViolation as exc:
        return _form_with_error(request, ctx, group, name, permissions, status_code=403,
                                error=f"{exc.violation.message}. Segregation of duties is enforced for this organization.")
    return None


@router.get("/new")
def new_security_group_form(
    request: Request, ctx: TenantContext = Depends(require_web_permission("security_group:manage"))
) -> HTMLResponse:
    return render_template(
        request, "security_groups/form.html", ctx=ctx, catalog=PERMISSION_CATALOG, group=None, selected=set()
    )


@router.post("")
def create_security_group(
    request: Request,
    name: str = Form(...),
    permissions: list[str] = Form([]),
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("security_group:manage")),
    _csrf: None = Depends(verify_csrf),
) -> RedirectResponse:
    if refused := _sod_refusal(request, db, ctx, None, name, permissions):
        return refused
    try:
        group = security_group_service.create_security_group(
            db, ctx.tenant_id, security_group_service.SecurityGroupInput(name=name, permissions=permissions)
        )
    except IntegrityError:
        db.rollback()
        return _form_with_error(request, ctx, None, name, permissions)
    audit_log_service.record_action(
        db,
        ctx.tenant_id,
        actor_user_id=ctx.user_id,
        channel="web",
        action="security_group.create",
        summary=f"Created security group {group.name} ({len(group.permissions)} permissions)",
        resource_type="security_group",
        resource_id=group.id,
    )
    db.commit()
    return RedirectResponse("/ui/security-groups?flash=Security+group+created.", status_code=303)


@router.get("/{group_id}/edit")
def edit_security_group_form(
    request: Request,
    group_id: uuid.UUID,
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("security_group:manage")),
) -> HTMLResponse:
    group = security_group_service.get_security_group(db, ctx.tenant_id, group_id)
    if group is None:
        raise WebNotFound()
    return render_template(
        request, "security_groups/form.html", ctx=ctx, catalog=PERMISSION_CATALOG, group=group, selected=set(group.permissions)
    )


@router.post("/{group_id}")
def update_security_group(
    request: Request,
    group_id: uuid.UUID,
    name: str = Form(...),
    permissions: list[str] = Form([]),
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("security_group:manage")),
    _csrf: None = Depends(verify_csrf),
) -> RedirectResponse:
    if refused := _sod_refusal(request, db, ctx, security_group_service.get_security_group(db, ctx.tenant_id, group_id), name, permissions):
        return refused
    try:
        group = security_group_service.update_security_group(
            db, ctx.tenant_id, group_id, security_group_service.SecurityGroupInput(name=name, permissions=permissions)
        )
        db.flush()
    except IntegrityError:
        db.rollback()
        return _form_with_error(request, ctx, security_group_service.get_security_group(db, ctx.tenant_id, group_id), name, permissions)
    if group is not None:
        audit_log_service.record_action(
            db,
            ctx.tenant_id,
            actor_user_id=ctx.user_id,
            channel="web",
            action="security_group.update",
            summary=f"Updated security group {group.name} ({len(group.permissions)} permissions)",
            resource_type="security_group",
            resource_id=group.id,
        )
    db.commit()
    if group is None:
        return RedirectResponse("/ui/security-groups?error=Security+group+not+found.", status_code=303)
    return RedirectResponse("/ui/security-groups?flash=Security+group+updated.", status_code=303)
