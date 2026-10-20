# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Sign-on: a customer's own administrators connecting its identity provider
(CUSTOMER_ADMIN_PLAN.md, CA3; services/customer_sign_on_service.py). The connection form is
the bank's own (sso/form.html), pointed at these routes and limited to the roles the bank
offers."""

import uuid
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy.orm import Session

from openclear.db.session import get_db
from openclear.db.tenancy import TenantContext
from openclear.domain.sso_connection import SsoProvider
from openclear.services import audit_log_service, customer_people_service, customer_sign_on_service, sso_service
from openclear.services.customer_sign_on_service import SignOnError
from openclear.web.deps import WebNotFound, render_template, require_web_permission
from openclear.web.redirects import see_other
from openclear.web.routers.sso_settings import SsoForm, _saml_urls, _scim_base, sso_form
from openclear.web.security import verify_csrf

router = APIRouter(prefix="/ui/sign-on", tags=["web-sign-on"])
_manage = require_web_permission("customer_sso:manage")
_BASE = "/ui/sign-on"


def _record(db: Session, ctx: TenantContext, action: str, summary: str) -> None:
    """Against the customer, so it's in the customer's activity page and the bank's action log."""
    audit_log_service.record_action(db, ctx.tenant_id, actor_user_id=ctx.user_id, channel="web", action=action, summary=summary,
                                     resource_type="customer", resource_id=ctx.customer_id)


def _test_url(ctx: TenantContext, connection_id: uuid.UUID) -> str:
    return f"/ui/login/sso/{connection_id}/start?" + urlencode({"tenant_id": str(ctx.tenant_id), "next": _BASE})


@router.get("")
def overview(request: Request, db: Session = Depends(get_db), ctx: TenantContext = Depends(_manage)) -> HTMLResponse:
    try:
        customer = customer_sign_on_service.customer(db, ctx.tenant_id, ctx.customer_id)
    except SignOnError:
        raise WebNotFound() from None
    connections = sso_service.list_connections(db, ctx.tenant_id, customer_id=ctx.customer_id)
    working = {c.id for c in sso_service.get_login_connections(db, ctx.tenant_id, customer_id=ctx.customer_id)}
    return render_template(
        request, "sign_on/index.html", ctx=ctx, customer=customer, connections=connections, working=working,
        test_urls={c.id: _test_url(ctx, c.id) for c in connections}, domains=customer_sign_on_service.domains(db, ctx.tenant_id, ctx.customer_id),
        txt_prefix=customer_sign_on_service.TXT_PREFIX,
    )


def _form(request: Request, db: Session, ctx: TenantContext, connection=None, *, error: str | None = None, status_code: int = 200,
          scim_token: str | None = None, scim_waiting=None) -> HTMLResponse:
    customer = customer_sign_on_service.customer(db, ctx.tenant_id, ctx.customer_id)
    response = render_template(
        request, "sso/form.html", ctx=ctx, back_path=_BASE, sso_base=_BASE, connection=connection, customer=customer,
        mappings=sso_service.list_group_mappings(db, ctx.tenant_id, connection.id) if connection else [],
        groups=customer_people_service.offered_groups(db, ctx.tenant_id), providers=list(SsoProvider), error=error,
        saml_urls=_saml_urls(request, connection) if connection else None, scim_base=_scim_base(request), scim_token=scim_token,
        scim_waiting=scim_waiting, customer_managed=True, status_code=status_code,
    )
    if scim_token:
        response.headers["Cache-Control"] = "no-store"
    return response


# Literal paths first: FastAPI matches in registration order, and "/{connection_id}" below
# would otherwise take "/domains" and "/password-sign-in".
def _change(db: Session, ctx: TenantContext, kind: str, payload: dict, summary: str | None = None):
    """A change to the customer's own sign-on through dual control (DUAL_CONTROL_PLAN.md DC3):
    (outcome, None), or (None, error)."""
    from openclear.services import change_request_service

    try:
        return change_request_service.request_or_apply(db, kind, payload, tenant_id=ctx.tenant_id, customer_id=ctx.customer_id,
                                                       user_id=ctx.user_id, summary=summary), None
    except ValueError as exc:
        db.rollback()
        return None, str(exc)


def _waiting(outcome) -> str:
    if outcome.change is not None and outcome.change.applies_at:
        return "Nobody else can approve this, so it takes effect in 24 hours unless you withdraw it."
    return "Sent for approval: it takes effect when another administrator, or your bank, approves it."


@router.post("/password-sign-in")
def password_sign_in(allowed: bool = Form(False), db: Session = Depends(get_db), ctx: TenantContext = Depends(_manage),
                     _csrf: None = Depends(verify_csrf)) -> Response:
    if allowed:
        # Passwords back on is a protection off: it needs a second person (DUAL_CONTROL_PLAN.md).
        outcome, error = _change(db, ctx, "customer.password_sign_in", {})
        if error:
            return see_other(_BASE, error=error)
        if outcome.waiting:
            db.commit()
            return see_other(_BASE, flash=_waiting(outcome))
        _record(db, ctx, "customer_sign_on.password_sign_in", "Password sign-in allowed")
        db.commit()
        return see_other(_BASE, flash="Password sign-in allowed.")
    try:
        customer_sign_on_service.set_password_sign_in(db, ctx.tenant_id, ctx.customer_id, enabled=allowed,
                                                      signed_in_through_sign_on=ctx.confined)
    except SignOnError as exc:
        db.rollback()
        return see_other(_BASE, error=str(exc))
    _record(db, ctx, "customer_sign_on.password_sign_in", f"Password sign-in {'allowed' if allowed else 'turned off'}")
    db.commit()
    return see_other(_BASE, flash="Password sign-in allowed." if allowed else "Password sign-in turned off: your people sign in through sign-on.")


@router.post("/domains")
def add_domain(domain: str = Form(""), db: Session = Depends(get_db), ctx: TenantContext = Depends(_manage),
               _csrf: None = Depends(verify_csrf)) -> Response:
    try:
        row = customer_sign_on_service.add_domain(db, ctx.tenant_id, ctx.customer_id, domain)
    except SignOnError as exc:
        db.rollback()
        return see_other(_BASE, error=str(exc))
    _record(db, ctx, "customer_sign_on.domain_added", f"Added the email domain {row.domain}")
    db.commit()
    return see_other(_BASE, flash=f"Added {row.domain}. Publish its TXT record, then press Verify.")


@router.post("/domains/move")
def move_domain(request: Request, from_domain: str = Form(""), to_domain: str = Form(""), db: Session = Depends(get_db),
                ctx: TenantContext = Depends(require_web_permission("customer_user:manage")), _csrf: None = Depends(verify_csrf)) -> Response:
    """Everyone at the old domain moves to the same name at a verified new one: a protected change,
    always approved by a second person (OWASP_PLAN.md OW-D33)."""
    from openclear.config import get_settings
    from openclear.services import change_request_service
    from openclear.web.routers.tenant_settings import waiting_message

    payload = {"from_domain": from_domain.strip().lower(), "to_domain": to_domain.strip().lower(),
               "base_url": get_settings().public_base_url or str(request.base_url)}
    try:
        outcome = change_request_service.request_or_apply(
            db, "customer.email_domain.move", payload, tenant_id=ctx.tenant_id, customer_id=ctx.customer_id, user_id=ctx.user_id,
            summary=f"Move everyone at {payload['from_domain']} to {payload['to_domain']}")
    except ValueError as exc:
        db.rollback()
        return see_other(_BASE, error=str(exc))
    db.commit()
    return see_other(_BASE, flash=outcome.summary if outcome.applied else waiting_message(outcome))


@router.get("/new")
def new_connection(request: Request, db: Session = Depends(get_db), ctx: TenantContext = Depends(_manage)) -> HTMLResponse:
    try:
        return _form(request, db, ctx)
    except SignOnError:
        raise WebNotFound() from None


@router.post("")
def create_connection(request: Request, form: SsoForm = Depends(sso_form), db: Session = Depends(get_db),
                      ctx: TenantContext = Depends(_manage), _csrf: None = Depends(verify_csrf)) -> Response:
    try:
        connection = customer_sign_on_service.create(db, ctx.tenant_id, ctx.customer_id, form.to_input(ctx.customer_id))
    except SignOnError as exc:
        db.rollback()
        return _form(request, db, ctx, error=str(exc), status_code=422)
    from openclear.services import change_request_service

    note = "Connection added. Map your identity provider's groups to roles below."
    if change_request_service.needs_approval(db, ctx.tenant_id, "customer.sign_on.activate", ctx.customer_id):
        connection.is_active = False  # on when a second person approves
        outcome, error = _change(db, ctx, "customer.sign_on.activate", {"connection_id": str(connection.id)},
                                 summary=f"Turn on the new sign-on connection {connection.display_name}")
        if error:
            return _form(request, db, ctx, error=error, status_code=422)
        note = f"Connection added, off for now. {_waiting(outcome)}"
    _record(db, ctx, "customer_sign_on.create", f"Added the sign-on connection {connection.display_name}")
    db.commit()
    return see_other(f"{_BASE}/{connection.id}/edit", flash=note)


def _connection(db: Session, ctx: TenantContext, connection_id: uuid.UUID, *, editable: bool = True):
    try:
        return customer_sign_on_service.connection(db, ctx.tenant_id, ctx.customer_id, connection_id, editable=editable)
    except SignOnError:
        raise WebNotFound() from None


@router.get("/{connection_id}/edit")
def edit_connection(request: Request, connection_id: uuid.UUID, db: Session = Depends(get_db), ctx: TenantContext = Depends(_manage)) -> HTMLResponse:
    return _form(request, db, ctx, _connection(db, ctx, connection_id))


@router.post("/{connection_id}")
def update_connection(request: Request, connection_id: uuid.UUID, form: SsoForm = Depends(sso_form), db: Session = Depends(get_db),
                      ctx: TenantContext = Depends(_manage), _csrf: None = Depends(verify_csrf)) -> Response:
    from openclear.web.routers.sso_settings import _input_payload

    connection = _connection(db, ctx, connection_id)
    data = form.to_input(ctx.customer_id)
    savepoint = db.begin_nested()  # checked now, so a mistake shows at once rather than on approval
    try:
        customer_sign_on_service.update(db, ctx.tenant_id, ctx.customer_id, connection_id, data)
    except SignOnError as exc:
        savepoint.rollback()
        return _form(request, db, ctx, _connection(db, ctx, connection_id), error=str(exc), status_code=422)
    savepoint.rollback()
    outcome, error = _change(db, ctx, "customer.sign_on.update", _input_payload(data, connection_id))
    if error:
        return _form(request, db, ctx, _connection(db, ctx, connection_id), error=error, status_code=422)
    if outcome.waiting:
        db.commit()
        return see_other(f"{_BASE}/{connection_id}/edit", flash=_waiting(outcome))
    _record(db, ctx, "customer_sign_on.update", f"Changed the sign-on connection {connection.display_name}")
    db.commit()
    return see_other(f"{_BASE}/{connection_id}/edit", flash="Saved.")


@router.post("/{connection_id}/deactivate")
@router.post("/{connection_id}/reactivate")
def set_active(request: Request, connection_id: uuid.UUID, db: Session = Depends(get_db), ctx: TenantContext = Depends(_manage),
               _csrf: None = Depends(verify_csrf)) -> Response:
    active = request.url.path.endswith("/reactivate")
    if active:
        connection = _connection(db, ctx, connection_id)
        outcome, error = _change(db, ctx, "customer.sign_on.activate", {"connection_id": str(connection_id)},
                                 summary=f"Turn on the sign-on connection {connection.display_name}")
        if error:
            return see_other(_BASE, error=error)
        if outcome.waiting:
            db.commit()
            return see_other(_BASE, flash=_waiting(outcome))
        _record(db, ctx, "customer_sign_on.reactivate", f"Turned on the sign-on connection {connection.display_name}")
        db.commit()
        return see_other(_BASE, flash="Connection turned on.")
    try:
        connection = customer_sign_on_service.set_active(db, ctx.tenant_id, ctx.customer_id, connection_id, active)
    except SignOnError as exc:
        db.rollback()
        return see_other(_BASE, error=str(exc))
    _record(db, ctx, "customer_sign_on.reactivate" if active else "customer_sign_on.deactivate",
            f"{'Turned on' if active else 'Turned off'} the sign-on connection {connection.display_name}")
    db.commit()
    return see_other(_BASE, flash="Connection turned on." if active else "Connection turned off.")


@router.post("/{connection_id}/mappings")
def add_mapping(connection_id: uuid.UUID, external_group: str = Form(""), security_group_id: uuid.UUID = Form(...),
                priority: int = Form(100), db: Session = Depends(get_db), ctx: TenantContext = Depends(_manage),
                _csrf: None = Depends(verify_csrf)) -> Response:
    connection = _connection(db, ctx, connection_id)
    outcome, error = _change(db, ctx, "customer.sign_on.mapping", {"connection_id": str(connection_id), "external_group": external_group.strip(),
                                                                    "security_group_id": str(security_group_id), "priority": priority},
                             summary=f"Give people in {external_group.strip()} a role")
    if error:
        return see_other(f"{_BASE}/{connection_id}/edit", error=error)
    if outcome.waiting:
        db.commit()
        return see_other(f"{_BASE}/{connection_id}/edit", flash=_waiting(outcome))
    _record(db, ctx, "customer_sign_on.mapping", f"Mapped the group {external_group.strip()} on {connection.display_name}")
    db.commit()
    return see_other(f"{_BASE}/{connection_id}/edit", flash="Mapping added.")


@router.post("/{connection_id}/mappings/{mapping_id}/delete")
def remove_mapping(connection_id: uuid.UUID, mapping_id: uuid.UUID, db: Session = Depends(get_db), ctx: TenantContext = Depends(_manage),
                   _csrf: None = Depends(verify_csrf)) -> Response:
    connection = _connection(db, ctx, connection_id)
    try:
        customer_sign_on_service.remove_mapping(db, ctx.tenant_id, ctx.customer_id, connection_id, mapping_id)
    except SignOnError as exc:
        db.rollback()
        return see_other(f"{_BASE}/{connection_id}/edit", error=str(exc))
    _record(db, ctx, "customer_sign_on.mapping_removed", f"Removed a group mapping from {connection.display_name}")
    db.commit()
    return see_other(f"{_BASE}/{connection_id}/edit", flash="Mapping removed.")


@router.post("/{connection_id}/scim-token")
def issue_scim_token(request: Request, connection_id: uuid.UUID, db: Session = Depends(get_db), ctx: TenantContext = Depends(_manage),
                     _csrf: None = Depends(verify_csrf)) -> HTMLResponse:
    from openclear.services import scim_service

    from openclear.services import change_request_service

    connection = _connection(db, ctx, connection_id)
    if change_request_service.needs_approval(db, ctx.tenant_id, "customer.scim_token.set", ctx.customer_id):
        # Shown once now; it works only when a second person approves (DUAL_CONTROL_PLAN.md).
        import secrets

        token = f"scim_{connection.tenant_id.hex}_{connection.id.hex}_{secrets.token_urlsafe(32)}"
        outcome, error = _change(db, ctx, "customer.scim_token.set", {"connection_id": str(connection.id), "token_hash": scim_service._hash(token)})
        if error:
            return see_other(f"{_BASE}/{connection_id}/edit", error=error)
        db.commit()
        return _form(request, db, ctx, connection, scim_token=token, scim_waiting=outcome.change)
    token = scim_service.issue_token(db, connection)
    _record(db, ctx, "customer_sign_on.scim_token", f"Issued a SCIM token for {connection.display_name}")
    db.commit()
    return _form(request, db, ctx, connection, scim_token=token)


@router.post("/{connection_id}/scim-token/revoke")
def revoke_scim_token(connection_id: uuid.UUID, db: Session = Depends(get_db), ctx: TenantContext = Depends(_manage),
                      _csrf: None = Depends(verify_csrf)) -> Response:
    from openclear.services import scim_service

    connection = _connection(db, ctx, connection_id)
    scim_service.revoke_token(db, connection)
    _record(db, ctx, "customer_sign_on.scim_revoked", f"Turned off SCIM for {connection.display_name}")
    db.commit()
    return see_other(f"{_BASE}/{connection_id}/edit", flash="SCIM turned off.")


@router.post("/domains/{domain_id}/verify")
def verify_domain(domain_id: uuid.UUID, db: Session = Depends(get_db), ctx: TenantContext = Depends(_manage),
                  _csrf: None = Depends(verify_csrf)) -> Response:
    try:
        row = customer_sign_on_service.verify_domain(db, ctx.tenant_id, ctx.customer_id, domain_id)
    except SignOnError as exc:
        db.rollback()
        return see_other(_BASE, error=str(exc))
    _record(db, ctx, "customer_sign_on.domain_verified", f"Verified the email domain {row.domain}")
    db.commit()
    return see_other(_BASE, flash=f"{row.domain} is verified.")


@router.post("/domains/{domain_id}/delete")
def remove_domain(domain_id: uuid.UUID, db: Session = Depends(get_db), ctx: TenantContext = Depends(_manage),
                  _csrf: None = Depends(verify_csrf)) -> Response:
    try:
        name = customer_sign_on_service.remove_domain(db, ctx.tenant_id, ctx.customer_id, domain_id)
    except SignOnError as exc:
        db.rollback()
        return see_other(_BASE, error=str(exc))
    _record(db, ctx, "customer_sign_on.domain_removed", f"Removed the email domain {name}")
    db.commit()
    return see_other(_BASE, flash=f"Removed {name}.")
