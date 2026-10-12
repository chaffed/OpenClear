# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Settings → API clients: credentials for the bank's own systems (SDK_PLAN.md P0;
services/api_client_service.py). A new or rotated secret is shown on the page that
creates it, once, and never again."""

import uuid

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.orm import Session

from openclear.db.session import get_db
from openclear.db.tenancy import TenantContext
from openclear.services import api_client_service, audit_log_service, customer_service, security_group_service, sso_service
from openclear.web.deps import WebNotFound, render_template, require_web_permission
from openclear.web.security import verify_csrf

router = APIRouter(prefix="/ui/admin/api-clients", tags=["web-api-clients"])
_manage = require_web_permission("api_client:manage")


def _form(request: Request, db: Session, ctx: TenantContext, *, error: str | None = None, values: dict | None = None, status_code: int = 200):
    return render_template(
        request, "admin/api_client_form.html", ctx=ctx, error=error, values=values or {}, status_code=status_code,
        groups=security_group_service.list_security_groups(db, ctx.tenant_id),
        customers=customer_service.list_customers(db, ctx.tenant_id),
        connections=[c for c in sso_service.list_connections(db, ctx.tenant_id) if c.protocol.value == "oidc"],
    )


def _secret_page(request: Request, ctx: TenantContext, issued, *, rotated: bool) -> Response:
    response = render_template(request, "admin/api_client_secret.html", ctx=ctx, client=issued.client, secret=issued.secret, rotated=rotated)
    response.headers["Cache-Control"] = "no-store"  # the secret must not sit in a cache
    return response


@router.get("")
def list_api_clients(request: Request, db: Session = Depends(get_db), ctx: TenantContext = Depends(_manage)) -> HTMLResponse:
    clients = [api_client_service.describe(db, c) for c in api_client_service.list_clients(db, ctx.tenant_id)]
    return render_template(request, "admin/api_clients.html", ctx=ctx, clients=clients)


@router.get("/new")
def new_api_client(request: Request, db: Session = Depends(get_db), ctx: TenantContext = Depends(_manage)) -> HTMLResponse:
    return _form(request, db, ctx)


@router.post("")
def create_api_client(
    request: Request,
    name: str = Form(""),
    security_group_id: str = Form(""),
    customer_id: str = Form(""),
    can_act_for_users: bool = Form(False),
    read_only: bool = Form(False),
    sso_connection_id: str = Form(""),
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(_manage),
    _csrf: None = Depends(verify_csrf),
) -> Response:
    values = {"name": name, "security_group_id": security_group_id, "customer_id": customer_id, "can_act_for_users": can_act_for_users,
              "sso_connection_id": sso_connection_id, "read_only": read_only}
    try:
        issued = api_client_service.create_client(
            db, ctx.tenant_id, name=name,
            security_group_id=uuid.UUID(security_group_id) if security_group_id else uuid.UUID(int=0),
            customer_id=uuid.UUID(customer_id) if customer_id else None,
            can_act_for_users=can_act_for_users, created_by_user_id=ctx.user_id,
            sso_connection_id=uuid.UUID(sso_connection_id) if sso_connection_id else None, read_only=read_only,
        )
    except (api_client_service.ApiClientError, ValueError) as exc:
        db.rollback()
        return _form(request, db, ctx, error=str(exc) if isinstance(exc, api_client_service.ApiClientError) else "Choose a security group.",
                     values=values, status_code=422)
    audit_log_service.record_action(
        db, ctx.tenant_id, actor_user_id=ctx.user_id, channel="web", action="api_client.create",
        summary=f"Created {'read-only ' if issued.client.read_only else ''}API client {issued.client.name} ({issued.client.client_id})",
        resource_type="api_client",
        resource_id=issued.client.id,
    )
    db.commit()
    return _secret_page(request, ctx, issued, rotated=False)


@router.post("/{api_client_id}/rotate")
def rotate_api_client_secret(
    request: Request, api_client_id: uuid.UUID, db: Session = Depends(get_db), ctx: TenantContext = Depends(_manage),
    _csrf: None = Depends(verify_csrf),
) -> Response:
    try:
        issued = api_client_service.rotate_secret(db, ctx.tenant_id, api_client_id)
    except api_client_service.ApiClientError:
        raise WebNotFound() from None
    audit_log_service.record_action(
        db, ctx.tenant_id, actor_user_id=ctx.user_id, channel="web", action="api_client.rotate_secret",
        summary=f"Rotated the secret of API client {issued.client.name}", resource_type="api_client", resource_id=issued.client.id,
    )
    db.commit()
    return _secret_page(request, ctx, issued, rotated=True)


@router.post("/{api_client_id}/revoke")
def revoke_api_client(
    api_client_id: uuid.UUID, db: Session = Depends(get_db), ctx: TenantContext = Depends(_manage), _csrf: None = Depends(verify_csrf),
) -> RedirectResponse:
    try:
        client = api_client_service.revoke(db, ctx.tenant_id, api_client_id)
    except api_client_service.ApiClientError:
        raise WebNotFound() from None
    audit_log_service.record_action(
        db, ctx.tenant_id, actor_user_id=ctx.user_id, channel="web", action="api_client.revoke",
        summary=f"Revoked API client {client.name}", resource_type="api_client", resource_id=client.id,
    )
    db.commit()
    return RedirectResponse("/ui/admin/api-clients?flash=API+client+revoked.", status_code=303)
