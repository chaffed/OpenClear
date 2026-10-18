# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import secrets
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
    customer_claim: str
    require_mfa_claim: bool = False
    mfa_values: str = ""

    def to_input(self, customer_id: uuid.UUID | None) -> SsoConnectionInput:
        return SsoConnectionInput(
            provider=self.provider, protocol=self.protocol, display_name=self.display_name, issuer=self.issuer,
            client_id=self.client_id or None, client_secret=self.client_secret or None,
            groups_claim_name=self.groups_claim_name or "groups", auto_provision=self.auto_provision, customer_id=customer_id,
            saml_sso_url=self.saml_sso_url, saml_slo_url=self.saml_slo_url, saml_idp_certificate=self.saml_idp_certificate,
            saml_email_attribute=self.saml_email_attribute, allow_idp_initiated=self.allow_idp_initiated,
            customer_claim=self.customer_claim if customer_id is None else None,
            require_mfa_claim=self.require_mfa_claim, mfa_values=self.mfa_values or None,
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
    customer_claim: str = Form(""),
    require_mfa_claim: bool = Form(False),
    mfa_values: str = Form(""),
) -> SsoForm:
    return SsoForm(
        provider, protocol, display_name, issuer, client_id, client_secret, groups_claim_name, auto_provision, saml_sso_url,
        saml_slo_url, saml_idp_certificate, saml_email_attribute, allow_idp_initiated, customer_claim, require_mfa_claim, mfa_values,
    )


def _saml_urls(request: Request, connection) -> list[tuple[str, str]] | None:
    """What the bank gives its identity provider for this connection, as (label, URL)."""
    from openclear.auth import saml_service
    from openclear.config import get_settings

    base = saml_service.base_url(str(request.base_url))
    if connection.protocol == SsoProtocol.SAML:
        urls = saml_service.sp_urls(base, connection)
        return [
            ("Entity ID (audience) and metadata URL", urls["entity_id"]),
            ("Reply URL (Assertion Consumer Service, HTTP-POST)", urls["acs"]),
            ("Single logout URL (HTTP-Redirect)", urls["slo"]),
        ]
    root = f"{base}/ui/sso/oidc/{connection.tenant_id}/{connection.id}"
    callback = f"{(get_settings().public_base_url or base).rstrip('/')}/ui/login/sso/{connection.id}/callback"
    return [
        ("Sign-in redirect URI", callback),
        ("Back-channel logout URI", f"{root}/backchannel-logout"),
        ("Initiate login URI (for a portal tile)", f"{root}/initiate"),
    ]


def _scim_base(request: Request) -> str:
    from openclear.config import get_settings

    return (get_settings().public_base_url or str(request.base_url)).rstrip("/") + "/scim/v2"


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


def _sso_change(db: Session, ctx: TenantContext, kind: str, payload: dict, *, summary: str | None = None):
    """A sign-on change through dual control (DUAL_CONTROL_PLAN.md): (outcome, None) or (None, error)."""
    from openclear.services import change_request_service

    try:
        return change_request_service.request_or_apply(db, kind, payload, tenant_id=ctx.tenant_id, user_id=ctx.user_id, summary=summary), None
    except ValueError as exc:
        db.rollback()
        return None, str(exc)


def _input_payload(data, connection_id) -> dict:
    """A connection's form, to wait in a change request: the client secret encrypted."""
    from dataclasses import asdict

    from openclear.auth.crypto import encrypt_secret

    fields = {k: (v.value if hasattr(v, "value") else (str(v) if isinstance(v, uuid.UUID) else v)) for k, v in asdict(data).items()}
    secret = fields.pop("client_secret", None)
    return {"connection_id": str(connection_id), "input": fields, **({"client_secret_encrypted": encrypt_secret(secret)} if secret else {})}


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
    from openclear.services import change_request_service
    from openclear.web.routers.tenant_settings import waiting_message

    note = "Connection created. Add a group mapping below."
    if change_request_service.needs_approval(db, ctx.tenant_id, "sso_connection.activate"):
        # Made off, and turned on when a second admin approves (DUAL_CONTROL_PLAN.md).
        connection.is_active = False
        outcome, error = _sso_change(db, ctx, "sso_connection.activate", {"connection_id": str(connection.id)},
                                     summary=f"Turn on the new sign-on connection {connection.display_name}")
        if error:
            return RedirectResponse(f"/ui/admin/sso?error={quote(error)}", status_code=303)
        note = f"Connection created, off for now. {waiting_message(outcome)}"
    audit_log_service.record_action(
        db, ctx.tenant_id, actor_user_id=ctx.user_id, channel="web", action="sso_connection.create",
        summary=f"Added SSO connection {connection.display_name!r}", resource_type="sso_connection", resource_id=connection.id,
    )
    db.commit()
    return RedirectResponse(f"/ui/admin/sso/{connection.id}/edit?flash={quote(note)}", status_code=303)


@router.post("/ui/admin/sso/password-login")
def set_bank_password_login(
    require_sso: bool = Form(False),
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("tenant:manage")),
    _csrf: None = Depends(verify_csrf),
) -> RedirectResponse:
    """Require single sign-on, or let staff use passwords again: that needs a second admin
    (DUAL_CONTROL_PLAN.md)."""
    from openclear.services import audit_log_service, change_request_service
    from openclear.web.routers.tenant_settings import waiting_message

    try:
        outcome = change_request_service.request_or_apply(db, "password_login", {"values": {"password_login_enabled": not require_sso}},
                                                          tenant_id=ctx.tenant_id, user_id=ctx.user_id)
    except ValueError as exc:
        db.rollback()
        return RedirectResponse(f"/ui/admin/sso?error={quote(str(exc))}", status_code=303)
    if outcome.waiting:
        db.commit()
        return RedirectResponse(f"/ui/admin/sso?flash={quote(waiting_message(outcome))}", status_code=303)
    if outcome.summary != "Nothing changed.":
        audit_log_service.record_action(db, ctx.tenant_id, actor_user_id=ctx.user_id, channel="web", action="sso.password_login",
                                        summary=outcome.summary, resource_type="tenant", resource_id=ctx.tenant_id)
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
        saml_urls=_saml_urls(request, connection), scim_base=_scim_base(request),
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
    from openclear.web.routers.tenant_settings import waiting_message

    data = form.to_input(None)
    connection = sso_service.get_connection(db, ctx.tenant_id, connection_id)
    if connection is None or connection.customer_id is not None:
        raise WebNotFound()
    savepoint = db.begin_nested()  # checked now, so a mistake shows at once rather than on approval
    try:
        sso_service.update_connection(db, ctx.tenant_id, connection_id, data)
    except (UnsafeUrlError, ValueError) as exc:
        savepoint.rollback()
        return RedirectResponse(f"/ui/admin/sso/{connection_id}/edit?error=" + quote(str(exc)), status_code=303)
    savepoint.rollback()
    outcome, error = _sso_change(db, ctx, "sso_connection.update", _input_payload(data, connection_id))
    if error:
        return RedirectResponse(f"/ui/admin/sso/{connection_id}/edit?error=" + quote(error), status_code=303)
    if outcome.waiting:
        db.commit()
        return RedirectResponse(f"/ui/admin/sso/{connection_id}/edit?flash={quote(waiting_message(outcome))}", status_code=303)
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
    from openclear.web.routers.tenant_settings import waiting_message

    connection = sso_service.get_connection(db, ctx.tenant_id, connection_id)
    if connection is None or connection.customer_id is not None:
        raise WebNotFound()
    outcome, error = _sso_change(db, ctx, "sso_connection.activate", {"connection_id": str(connection_id)},
                                 summary=f"Turn on the sign-on connection {connection.display_name}")
    if error:
        return RedirectResponse(f"/ui/admin/sso?error={quote(error)}", status_code=303)
    db.commit()
    return RedirectResponse(f"/ui/admin/sso?flash={quote(waiting_message(outcome) if outcome.waiting else 'Connection reactivated.')}",
                            status_code=303)


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
    from openclear.web.routers.tenant_settings import waiting_message

    group = security_group_service.get_security_group(db, ctx.tenant_id, security_group_id)
    outcome, error = _sso_change(db, ctx, "sso_mapping.add", {"connection_id": str(connection_id), "external_group": external_group.strip(),
                                                               "security_group_id": str(security_group_id), "priority": priority},
                                 summary=f"Give people in {external_group.strip()} the group {group.name if group else 'chosen'}")
    if error:
        return RedirectResponse(f"/ui/admin/sso/{connection_id}/edit?error={quote(error)}", status_code=303)
    if outcome.waiting:
        db.commit()
        return RedirectResponse(f"/ui/admin/sso/{connection_id}/edit?flash={quote(waiting_message(outcome))}", status_code=303)
    audit_log_service.record_action(db, ctx.tenant_id, actor_user_id=ctx.user_id, channel="web", action="sso_connection.mapping",
                                    summary=outcome.summary, resource_type="sso_connection", resource_id=connection_id)
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
        scim_base=_scim_base(request),
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


# --- SCIM tokens (services/scim_service.py) ---


def _scim_page(request: Request, db: Session, ctx: TenantContext, connection, back_path: str, token: str | None, customer=None,
               waiting=None):
    response = render_template(
        request, "sso/form.html", ctx=ctx, back_path=back_path, connection=connection,
        mappings=sso_service.list_group_mappings(db, ctx.tenant_id, connection.id),
        groups=security_group_service.list_security_groups(db, ctx.tenant_id), providers=list(SsoProvider), error=None,
        saml_urls=_saml_urls(request, connection), scim_token=token, scim_base=_scim_base(request), scim_waiting=waiting,
        **({"customer": customer} if customer else {}),
    )
    response.headers["Cache-Control"] = "no-store"
    return response


def _scim_change(db: Session, ctx: TenantContext, connection, issue: bool) -> str | None:
    from openclear.services import scim_service

    token = scim_service.issue_token(db, connection) if issue else None
    if not issue:
        scim_service.revoke_token(db, connection)
    audit_log_service.record_action(
        db, ctx.tenant_id, actor_user_id=ctx.user_id, channel="web",
        action="sso_connection.scim_token" if issue else "sso_connection.scim_revoke",
        summary=("Generated a SCIM token" if issue else "Turned off SCIM") + f" for {connection.display_name}",
        resource_type="sso_connection", resource_id=connection.id,
    )
    db.commit()
    return token


@router.post("/ui/admin/sso/{connection_id}/scim-token")
def generate_bank_scim_token(
    request: Request, connection_id: uuid.UUID, db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("tenant:manage")), _csrf: None = Depends(verify_csrf),
) -> HTMLResponse:
    from openclear.services import change_request_service, scim_service

    connection = sso_service.get_connection(db, ctx.tenant_id, connection_id)
    if connection is None or connection.customer_id is not None:
        raise WebNotFound()
    if not change_request_service.needs_approval(db, ctx.tenant_id, "scim_token.set"):
        token = _scim_change(db, ctx, connection, issue=True)
        return _scim_page(request, db, ctx, connection, "/ui/admin/sso", token)
    # Shown once now, but it works only when a second admin approves: the request holds just its hash (DUAL_CONTROL_PLAN.md).
    token = f"scim_{connection.tenant_id.hex}_{connection.id.hex}_{secrets.token_urlsafe(32)}"
    outcome, error = _sso_change(db, ctx, "scim_token.set", {"connection_id": str(connection.id), "token_hash": scim_service._hash(token)},
                                 summary=f"Let a directory provision people through {connection.display_name}")
    if error:
        return RedirectResponse(f"/ui/admin/sso/{connection_id}/edit?error={quote(error)}", status_code=303)
    db.commit()
    return _scim_page(request, db, ctx, connection, "/ui/admin/sso", token, waiting=outcome.change)


@router.post("/ui/admin/sso/{connection_id}/scim-token/revoke")
def revoke_bank_scim_token(
    connection_id: uuid.UUID, db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("tenant:manage")), _csrf: None = Depends(verify_csrf),
) -> RedirectResponse:
    connection = sso_service.get_connection(db, ctx.tenant_id, connection_id)
    if connection is None or connection.customer_id is not None:
        raise WebNotFound()
    _scim_change(db, ctx, connection, issue=False)
    return RedirectResponse(f"/ui/admin/sso/{connection_id}/edit?flash=SCIM+turned+off.", status_code=303)


@router.post("/ui/customers/{customer_id}/sso/{connection_id}/scim-token")
def generate_customer_scim_token(
    request: Request, customer_id: uuid.UUID, connection_id: uuid.UUID, db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("customer:manage")), _csrf: None = Depends(verify_csrf),
) -> HTMLResponse:
    customer = _get_customer_or_404(db, ctx.tenant_id, customer_id)
    connection = sso_service.get_connection(db, ctx.tenant_id, connection_id)
    if connection is None or connection.customer_id != customer_id:
        raise WebNotFound()
    token = _scim_change(db, ctx, connection, issue=True)
    return _scim_page(request, db, ctx, connection, f"/ui/customers/{customer_id}/sso", token, customer=customer)


@router.post("/ui/customers/{customer_id}/sso/{connection_id}/scim-token/revoke")
def revoke_customer_scim_token(
    customer_id: uuid.UUID, connection_id: uuid.UUID, db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("customer:manage")), _csrf: None = Depends(verify_csrf),
) -> RedirectResponse:
    _get_customer_or_404(db, ctx.tenant_id, customer_id)
    connection = sso_service.get_connection(db, ctx.tenant_id, connection_id)
    if connection is None or connection.customer_id != customer_id:
        raise WebNotFound()
    _scim_change(db, ctx, connection, issue=False)
    return RedirectResponse(f"/ui/customers/{customer_id}/sso/{connection_id}/edit?flash=SCIM+turned+off.", status_code=303)
