# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import uuid
from dataclasses import dataclass
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from openclear.auth.outbound_http import UnsafeUrlError
from openclear.db.session import get_db
from openclear.db.tenancy import TenantContext
from openclear.domain.sso_connection import SsoProtocol, SsoProvider
from openclear.domain.tenant import Tenant
from openclear.services import audit_log_service, customer_service, security_group_service, sso_service
from openclear.services.sso_service import SsoConnectionInput
from openclear.web.deps import WebNotFound, render_template, require_web_permission
from openclear.web.security import verify_csrf

router = APIRouter(tags=["web-sso"])


@dataclass
class SsoForm:
    """The connection form's fields, OIDC and SAML (sso/form.html)."""

    provider: SsoProvider
    protocol: SsoProtocol
    display_name: str
    issuer: str
    client_id: str
    client_secret: str
    groups_claim_name: str
    auto_provision: bool
    saml_sso_url: str
    saml_slo_url: str
    saml_idp_certificate: str
    saml_email_attribute: str
    allow_idp_initiated: bool

    def to_input(self, customer_id: uuid.UUID | None) -> SsoConnectionInput:
        return SsoConnectionInput(
            provider=self.provider, protocol=self.protocol, display_name=self.display_name, issuer=self.issuer,
            client_id=self.client_id or None, client_secret=self.client_secret or None,
            groups_claim_name=self.groups_claim_name or "groups", auto_provision=self.auto_provision, customer_id=customer_id,
            saml_sso_url=self.saml_sso_url, saml_slo_url=self.saml_slo_url, saml_idp_certificate=self.saml_idp_certificate,
            saml_email_attribute=self.saml_email_attribute, allow_idp_initiated=self.allow_idp_initiated,
        )


def sso_form(
    provider: SsoProvider = Form(...),
    protocol: SsoProtocol = Form(SsoProtocol.OIDC),
    display_name: str = Form(...),
    issuer: str = Form(...),
    client_id: str = Form(""),
    client_secret: str = Form(""),
    groups_claim_name: str = Form("groups"),
    auto_provision: bool = Form(False),
    saml_sso_url: str = Form(""),
    saml_slo_url: str = Form(""),
    saml_idp_certificate: str = Form(""),
    saml_email_attribute: str = Form(""),
    allow_idp_initiated: bool = Form(False),
) -> SsoForm:
    return SsoForm(
        provider, protocol, display_name, issuer, client_id, client_secret, groups_claim_name, auto_provision, saml_sso_url,
        saml_slo_url, saml_idp_certificate, saml_email_attribute, allow_idp_initiated,
    )


def _saml_urls(request: Request, connection) -> dict | None:
    """What the bank gives its identity provider for a SAML connection."""
    if connection.protocol != SsoProtocol.SAML:
        return None
    from openclear.auth import saml_service

    return saml_service.sp_urls(saml_service.base_url(str(request.base_url)), connection)


def _tenant_password_login_enabled(db: Session, tenant_id: uuid.UUID) -> bool:
    tenant = db.get(Tenant, tenant_id)
    return bool(tenant and tenant.password_login_enabled)


# --- Bank-wide: /ui/admin/sso/* (tenant:manage -- lives under /ui/admin so it's
# reachable via the Admin nav link, but stays gated on tenant:manage, not admin:manage;
# see web/routers/admin.py's require_any_web_permission gate on GET /ui/admin) ---


@router.get("/ui/admin/sso")
def list_bank_connections(
    request: Request, db: Session = Depends(get_db), ctx: TenantContext = Depends(require_web_permission("tenant:manage"))
) -> HTMLResponse:
    connections = sso_service.list_connections(db, ctx.tenant_id, customer_id=None)
    return render_template(
        request, "admin/sso.html", ctx=ctx, connections=connections,
        password_login_enabled=_tenant_password_login_enabled(db, ctx.tenant_id),
    )


@router.get("/ui/admin/sso/new")
def new_bank_connection_form(
    request: Request, db: Session = Depends(get_db), ctx: TenantContext = Depends(require_web_permission("tenant:manage"))
) -> HTMLResponse:
    return render_template(
        request, "sso/form.html", ctx=ctx, back_path="/ui/admin/sso", connection=None, mappings=[],
        groups=security_group_service.list_security_groups(db, ctx.tenant_id), providers=list(SsoProvider), error=None,
    )


@router.post("/ui/admin/sso")
def create_bank_connection(
    request: Request,
    form: SsoForm = Depends(sso_form),
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("tenant:manage")),
    _csrf: None = Depends(verify_csrf),
) -> HTMLResponse:
    data = form.to_input(None)
    try:
        connection = sso_service.create_connection(db, ctx.tenant_id, data)
    except ValueError as exc:
        db.rollback()
        return render_template(
            request, "sso/form.html", ctx=ctx, back_path="/ui/admin/sso", connection=None, mappings=[],
            groups=security_group_service.list_security_groups(db, ctx.tenant_id), providers=list(SsoProvider),
            error=str(exc), status_code=422,
        )
    audit_log_service.record_action(
        db, ctx.tenant_id, actor_user_id=ctx.user_id, channel="web", action="sso_connection.create",
        summary=f"Added SSO connection {connection.display_name!r}", resource_type="sso_connection", resource_id=connection.id,
    )
    db.commit()
    return RedirectResponse(f"/ui/admin/sso/{connection.id}/edit?flash=Connection+created.+Add+a+group+mapping+below.", status_code=303)


@router.post("/ui/admin/sso/password-login")
def set_bank_password_login(
    require_sso: bool = Form(False),
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("tenant:manage")),
    _csrf: None = Depends(verify_csrf),
) -> RedirectResponse:
    try:
        sso_service.set_tenant_password_login_enabled(db, ctx.tenant_id, enabled=not require_sso)
    except ValueError as exc:
        db.rollback()
        return RedirectResponse(f"/ui/admin/sso?error={quote(str(exc))}", status_code=303)
    db.commit()
    return RedirectResponse("/ui/admin/sso?flash=Updated.", status_code=303)


@router.get("/ui/admin/sso/{connection_id}/edit")
def edit_bank_connection_form(
    request: Request, connection_id: uuid.UUID, db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("tenant:manage")),
) -> HTMLResponse:
    connection = sso_service.get_connection(db, ctx.tenant_id, connection_id)
    if connection is None:
        raise WebNotFound()
    mappings = sso_service.list_group_mappings(db, ctx.tenant_id, connection_id)
    return render_template(
        request, "sso/form.html", ctx=ctx, back_path="/ui/admin/sso", connection=connection, mappings=mappings,
        groups=security_group_service.list_security_groups(db, ctx.tenant_id), providers=list(SsoProvider), error=None,
        saml_urls=_saml_urls(request, connection),
    )


@router.post("/ui/admin/sso/{connection_id}")
def update_bank_connection(
    request: Request,
    connection_id: uuid.UUID,
    form: SsoForm = Depends(sso_form),
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("tenant:manage")),
    _csrf: None = Depends(verify_csrf),
) -> HTMLResponse:
    data = form.to_input(None)
    try:
        sso_service.update_connection(db, ctx.tenant_id, connection_id, data)
    except UnsafeUrlError as exc:
        db.rollback()
        return RedirectResponse(f"/ui/admin/sso/{connection_id}/edit?error=" + quote(str(exc)), status_code=303)
    except ValueError:
        # customer_id is None here, so any other ValueError is "Connection not found".
        db.rollback()
        raise WebNotFound() from None
    audit_log_service.record_action(
        db, ctx.tenant_id, actor_user_id=ctx.user_id, channel="web", action="sso_connection.update",
        summary="Updated SSO connection", resource_type="sso_connection", resource_id=connection_id,
    )
    db.commit()
    return RedirectResponse(f"/ui/admin/sso/{connection_id}/edit?flash=Saved.", status_code=303)


@router.post("/ui/admin/sso/{connection_id}/deactivate")
def deactivate_bank_connection(
    connection_id: uuid.UUID, db: Session = Depends(get_db), ctx: TenantContext = Depends(require_web_permission("tenant:manage")),
    _csrf: None = Depends(verify_csrf),
) -> RedirectResponse:
    if sso_service.deactivate_connection(db, ctx.tenant_id, connection_id) is None:
        raise WebNotFound()
    db.commit()
    return RedirectResponse("/ui/admin/sso?flash=Connection+deactivated.", status_code=303)


@router.post("/ui/admin/sso/{connection_id}/reactivate")
def reactivate_bank_connection(
    connection_id: uuid.UUID, db: Session = Depends(get_db), ctx: TenantContext = Depends(require_web_permission("tenant:manage")),
    _csrf: None = Depends(verify_csrf),
) -> RedirectResponse:
    if sso_service.reactivate_connection(db, ctx.tenant_id, connection_id) is None:
        raise WebNotFound()
    db.commit()
    return RedirectResponse("/ui/admin/sso?flash=Connection+reactivated.", status_code=303)


@router.post("/ui/admin/sso/{connection_id}/mappings")
def add_bank_group_mapping(
    connection_id: uuid.UUID,
    external_group: str = Form(...),
    security_group_id: uuid.UUID = Form(...),
    priority: int = Form(100),
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("tenant:manage")),
    _csrf: None = Depends(verify_csrf),
) -> RedirectResponse:
    try:
        sso_service.add_group_mapping(
            db, ctx.tenant_id, connection_id, external_group=external_group, security_group_id=security_group_id, priority=priority
        )
    except ValueError as exc:
        db.rollback()
        return RedirectResponse(f"/ui/admin/sso/{connection_id}/edit?error={quote(str(exc))}", status_code=303)
    db.commit()
    return RedirectResponse(f"/ui/admin/sso/{connection_id}/edit?flash=Mapping+added.", status_code=303)


@router.post("/ui/admin/sso/{connection_id}/mappings/{mapping_id}/delete")
def remove_bank_group_mapping(
    connection_id: uuid.UUID, mapping_id: uuid.UUID, db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("tenant:manage")), _csrf: None = Depends(verify_csrf),
) -> RedirectResponse:
    sso_service.remove_group_mapping(db, ctx.tenant_id, mapping_id)
    db.commit()
    return RedirectResponse(f"/ui/admin/sso/{connection_id}/edit?flash=Mapping+removed.", status_code=303)


# --- Per-customer: /ui/customers/{customer_id}/sso/* (customer:manage) ---


def _get_customer_or_404(db: Session, tenant_id: uuid.UUID, customer_id: uuid.UUID):
    customer = customer_service.get_customer(db, tenant_id, customer_id)
    if customer is None:
        raise WebNotFound()
    return customer


@router.get("/ui/customers/{customer_id}/sso")
def list_customer_connections(
    request: Request, customer_id: uuid.UUID, db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("customer:manage")),
) -> HTMLResponse:
    customer = _get_customer_or_404(db, ctx.tenant_id, customer_id)
    connections = sso_service.list_connections(db, ctx.tenant_id, customer_id=customer_id)
    return render_template(
        request, "customers/sso.html", ctx=ctx, customer=customer, connections=connections,
        password_login_enabled=customer.password_login_enabled,
    )


@router.get("/ui/customers/{customer_id}/sso/new")
def new_customer_connection_form(
    request: Request, customer_id: uuid.UUID, db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("customer:manage")),
) -> HTMLResponse:
    customer = _get_customer_or_404(db, ctx.tenant_id, customer_id)
    return render_template(
        request, "sso/form.html", ctx=ctx, back_path=f"/ui/customers/{customer_id}/sso", connection=None, mappings=[],
        groups=security_group_service.list_security_groups(db, ctx.tenant_id), providers=list(SsoProvider), error=None,
        customer=customer,
    )


@router.post("/ui/customers/{customer_id}/sso")
def create_customer_connection(
    request: Request,
    customer_id: uuid.UUID,
    form: SsoForm = Depends(sso_form),
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("customer:manage")),
    _csrf: None = Depends(verify_csrf),
) -> HTMLResponse:
    customer = _get_customer_or_404(db, ctx.tenant_id, customer_id)
    data = form.to_input(customer_id)
    try:
        connection = sso_service.create_connection(db, ctx.tenant_id, data)
    except ValueError as exc:
        db.rollback()
        return render_template(
            request, "sso/form.html", ctx=ctx, back_path=f"/ui/customers/{customer_id}/sso", connection=None, mappings=[],
            groups=security_group_service.list_security_groups(db, ctx.tenant_id), providers=list(SsoProvider),
            error=str(exc), status_code=422, customer=customer,
        )
    audit_log_service.record_action(
        db, ctx.tenant_id, actor_user_id=ctx.user_id, channel="web", action="sso_connection.create",
        summary=f"Added SSO connection {connection.display_name!r} for customer {customer.name}",
        resource_type="sso_connection", resource_id=connection.id,
    )
    db.commit()
    return RedirectResponse(f"/ui/customers/{customer_id}/sso/{connection.id}/edit?flash=Connection+created.+Add+a+group+mapping+below.", status_code=303)


@router.post("/ui/customers/{customer_id}/sso/password-login")
def set_customer_password_login(
    customer_id: uuid.UUID,
    require_sso: bool = Form(False),
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("customer:manage")),
    _csrf: None = Depends(verify_csrf),
) -> RedirectResponse:
    _get_customer_or_404(db, ctx.tenant_id, customer_id)
    try:
        sso_service.set_customer_password_login_enabled(db, ctx.tenant_id, customer_id, enabled=not require_sso)
    except ValueError as exc:
        db.rollback()
        return RedirectResponse(f"/ui/customers/{customer_id}/sso?error={quote(str(exc))}", status_code=303)
    db.commit()
    return RedirectResponse(f"/ui/customers/{customer_id}/sso?flash=Updated.", status_code=303)


@router.get("/ui/customers/{customer_id}/sso/{connection_id}/edit")
def edit_customer_connection_form(
    request: Request, customer_id: uuid.UUID, connection_id: uuid.UUID, db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("customer:manage")),
) -> HTMLResponse:
    customer = _get_customer_or_404(db, ctx.tenant_id, customer_id)
    connection = sso_service.get_connection(db, ctx.tenant_id, connection_id)
    if connection is None or connection.customer_id != customer_id:
        raise WebNotFound()
    mappings = sso_service.list_group_mappings(db, ctx.tenant_id, connection_id)
    return render_template(
        request, "sso/form.html", ctx=ctx, back_path=f"/ui/customers/{customer_id}/sso", connection=connection,
        mappings=mappings, groups=security_group_service.list_security_groups(db, ctx.tenant_id),
        providers=list(SsoProvider), error=None, customer=customer, saml_urls=_saml_urls(request, connection),
    )


@router.post("/ui/customers/{customer_id}/sso/{connection_id}")
def update_customer_connection(
    request: Request,
    customer_id: uuid.UUID,
    connection_id: uuid.UUID,
    form: SsoForm = Depends(sso_form),
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("customer:manage")),
    _csrf: None = Depends(verify_csrf),
) -> RedirectResponse:
    customer = _get_customer_or_404(db, ctx.tenant_id, customer_id)
    existing = sso_service.get_connection(db, ctx.tenant_id, connection_id)
    if existing is None or existing.customer_id != customer_id:
        raise WebNotFound()
    data = form.to_input(customer_id)
    try:
        sso_service.update_connection(db, ctx.tenant_id, connection_id, data)
    except UnsafeUrlError as exc:
        db.rollback()
        return RedirectResponse(
            f"/ui/customers/{customer_id}/sso/{connection_id}/edit?error=" + quote(str(exc)), status_code=303
        )
    audit_log_service.record_action(
        db, ctx.tenant_id, actor_user_id=ctx.user_id, channel="web", action="sso_connection.update",
        summary=f"Updated SSO connection for customer {customer.name}", resource_type="sso_connection", resource_id=connection_id,
    )
    db.commit()
    return RedirectResponse(f"/ui/customers/{customer_id}/sso/{connection_id}/edit?flash=Saved.", status_code=303)


@router.post("/ui/customers/{customer_id}/sso/{connection_id}/deactivate")
def deactivate_customer_connection(
    customer_id: uuid.UUID, connection_id: uuid.UUID, db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("customer:manage")), _csrf: None = Depends(verify_csrf),
) -> RedirectResponse:
    _get_customer_or_404(db, ctx.tenant_id, customer_id)
    connection = sso_service.deactivate_connection(db, ctx.tenant_id, connection_id)
    if connection is None or connection.customer_id != customer_id:
        raise WebNotFound()
    db.commit()
    return RedirectResponse(f"/ui/customers/{customer_id}/sso?flash=Connection+deactivated.", status_code=303)


@router.post("/ui/customers/{customer_id}/sso/{connection_id}/reactivate")
def reactivate_customer_connection(
    customer_id: uuid.UUID, connection_id: uuid.UUID, db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("customer:manage")), _csrf: None = Depends(verify_csrf),
) -> RedirectResponse:
    _get_customer_or_404(db, ctx.tenant_id, customer_id)
    connection = sso_service.reactivate_connection(db, ctx.tenant_id, connection_id)
    if connection is None or connection.customer_id != customer_id:
        raise WebNotFound()
    db.commit()
    return RedirectResponse(f"/ui/customers/{customer_id}/sso?flash=Connection+reactivated.", status_code=303)


@router.post("/ui/customers/{customer_id}/sso/{connection_id}/mappings")
def add_customer_group_mapping(
    customer_id: uuid.UUID,
    connection_id: uuid.UUID,
    external_group: str = Form(...),
    security_group_id: uuid.UUID = Form(...),
    priority: int = Form(100),
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("customer:manage")),
    _csrf: None = Depends(verify_csrf),
) -> RedirectResponse:
    _get_customer_or_404(db, ctx.tenant_id, customer_id)
    try:
        sso_service.add_group_mapping(
            db, ctx.tenant_id, connection_id, external_group=external_group, security_group_id=security_group_id, priority=priority
        )
    except ValueError as exc:
        db.rollback()
        return RedirectResponse(f"/ui/customers/{customer_id}/sso/{connection_id}/edit?error={quote(str(exc))}", status_code=303)
    db.commit()
    return RedirectResponse(f"/ui/customers/{customer_id}/sso/{connection_id}/edit?flash=Mapping+added.", status_code=303)


@router.post("/ui/customers/{customer_id}/sso/{connection_id}/mappings/{mapping_id}/delete")
def remove_customer_group_mapping(
    customer_id: uuid.UUID, connection_id: uuid.UUID, mapping_id: uuid.UUID, db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("customer:manage")), _csrf: None = Depends(verify_csrf),
) -> RedirectResponse:
    _get_customer_or_404(db, ctx.tenant_id, customer_id)
    sso_service.remove_group_mapping(db, ctx.tenant_id, mapping_id)
    db.commit()
    return RedirectResponse(f"/ui/customers/{customer_id}/sso/{connection_id}/edit?flash=Mapping+removed.", status_code=303)
