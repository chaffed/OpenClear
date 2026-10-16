# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Settings: the control panel (web/control_panel.py; UX_PLAN.md U11) and its small
settings. Each setting has its own URL, /ui/settings/<key>: a plain page, or with
?partial=1 just the form, which the panel shows in a modal (static/js/control-panel.js).
Saving redirects to the panel with a message; an error shows the setting again with it."""

from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy.orm import Session

from openclear.config import get_settings
from openclear.db.session import get_db
from openclear.db.tenancy import TenantContext
from openclear.domain.bulk_upload_file import BulkUploadFile, BulkUploadSource
from openclear.domain.customer import Customer
from openclear.domain.tenant import Tenant
from openclear.repositories.bulk_upload_file_repo import BulkUploadFileRepository
from openclear.services import audit_log_service, reminder_service
from openclear.services.siem import delivery, formats, senders
from openclear.services.tenant_service import (
    InvalidTenantSettingsInput,
    set_data_export_timeout,
    set_messages,
    set_password_policy,
    set_require_dual_control,
    set_session_timeouts,
    update_tenant_branding,
    update_tenant_contact_info,
)
from openclear.web import control_panel
from openclear.web.deps import WebForbidden, get_web_context, render_template, require_web_permission
from openclear.web.security import verify_csrf

router = APIRouter(prefix="/ui/settings", tags=["web-tenant-settings"])

SAVED = "/ui/settings?flash=Settings+updated."

# The settings this router shows itself: key → title. Others in the panel (segregation of
# duties, the fraud model, data export, the admin pages) have routes of their own.
SECTIONS = {
    "branding": "Branding",
    "contact": "Contact info",
    "messages": "Messages",
    "password-policy": "Password policy",
    "timeouts": "Timeouts",
    "dual-control": "Dual control",
    "customer-approval": "New customer users",
    "outside-services": "Outside services",
    "standards": "Your standards",
    "reminders": "Reminders",
    "embedding": "Embedding in online banking",
    "decision-files": "Decision files",
    "security-events": "Security event forwarding",
    "auto-import": "Auto-import",
}


def _saved(request: Request, message: str = "Settings updated.", key: str | None = None):
    """Back to the panel with a message; the panel's script, which saves in the background,
    gets the message as JSON instead."""
    if request.headers.get("x-requested-with") == "fetch":
        return JSONResponse({"saved": True, "message": message, "key": key})
    return RedirectResponse(f"/ui/settings?flash={quote(message)}" + (f"&from={key}" if key else ""), status_code=303)


# --- The panel -----------------------------------------------------------------------------------


def require_panel(ctx: TenantContext = Depends(get_web_context)) -> TenantContext:
    if not control_panel.can_see_panel(ctx):
        raise WebForbidden()
    return ctx


@router.get("")
def panel(request: Request, db: Session = Depends(get_db), ctx: TenantContext = Depends(require_panel)) -> HTMLResponse:
    tiles = control_panel.tiles_for(db, ctx)
    if not tiles:
        raise WebForbidden()
    demo = False
    if ctx.customer_id is None and "admin:manage" in ctx.permissions:
        from openclear.services import demo_tenant_service

        demo_tenant = demo_tenant_service.get_demo_tenant(db)
        demo = demo_tenant is not None and demo_tenant.id == ctx.tenant_id
    return render_template(request, "settings/panel.html", ctx=ctx, sections=control_panel.grouped(tiles), is_demo_tenant=demo)


# --- One setting, as a page or as a modal's content ---------------------------------------------


def _section_permission(ctx: TenantContext, key: str) -> None:
    if key == "reminders" and ctx.customer_id is not None:
        allowed = "customer_banner:manage" in ctx.permissions
    else:
        allowed = "tenant:manage" in ctx.permissions
    if not allowed:
        from openclear.auth.deps import _refused

        _refused("access.denied", ctx, "tenant:manage", key="tenant:manage")
        raise WebForbidden()


def _section_context(db: Session, ctx: TenantContext, key: str) -> dict:
    settings = get_settings()
    tenant = db.get(Tenant, ctx.tenant_id)
    if key == "branding":
        return {"tenant_display_name": tenant.name, "accent_color": tenant.accent_color or ""}
    if key == "contact":
        return {f: getattr(tenant, f) for f in ("support_email", "support_phone", "website", "address_line1", "address_line2",
                                               "city", "state", "postal_code")}
    if key == "messages":
        return {"login_message": tenant.login_message or "", "banner_message": tenant.banner_message or ""}
    if key == "password-policy":
        return _password_policy_kwargs(tenant)
    if key == "timeouts":
        return {
            "access_token_expire_minutes": tenant.access_token_expire_minutes,
            "refresh_token_expire_minutes": tenant.refresh_token_expire_minutes,
            "default_access_token_expire_minutes": settings.jwt_access_token_expire_minutes,
            "default_refresh_token_expire_minutes": settings.jwt_refresh_token_expire_minutes,
            "data_export_timeout_seconds": tenant.data_export_timeout_seconds,
            "default_data_export_timeout_seconds": settings.data_export_timeout_seconds,
        }
    if key == "dual-control":
        return {"require_dual_control": tenant.require_dual_control}
    if key == "outside-services":
        from openclear.services import outside_services

        return {"services": outside_services.in_use(db, ctx.tenant_id), "review_every_days": outside_services.REVIEW_EVERY_DAYS}
    if key == "standards":
        return {"standard_password_min_length": tenant.standard_password_min_length, "key_rotation_days": tenant.key_rotation_days,
                "password_min_length": tenant.password_min_length}
    if key == "customer-approval":
        from sqlalchemy import select

        from openclear.domain.customer import Customer

        overrides = db.execute(select(Customer).where(Customer.tenant_id == ctx.tenant_id, Customer.approve_users.is_not(None))
                               .order_by(Customer.name)).scalars().all()
        return {"approve_customer_users": tenant.approve_customer_users, "overrides": overrides}
    if key == "embedding":
        return {"embed_allowed_origins": tenant.embed_allowed_origins or ""}
    if key == "decision-files":
        return _outbox_kwargs(db, ctx.tenant_id)
    if key == "auto-import":
        # Read-only: auto_import_* is one process-wide setting (config.py), so there's
        # nothing to save; just the configuration and this bank's own recent activity.
        return {
            "auto_import_enabled": settings.auto_import_enabled,
            "auto_import_interval_seconds": settings.auto_import_interval_seconds,
            "recent_auto_imports": BulkUploadFileRepository(db, ctx.tenant_id).list(
                source=BulkUploadSource.AUTO_IMPORT, limit=5, order_by=BulkUploadFile.uploaded_at.desc()),
        }
    if key == "reminders":
        return _reminder_kwargs(db, ctx, tenant)
    if key == "security-events":
        destination = delivery.bank_destination(db, ctx.tenant_id)
        return {
            "destination": destination, "kinds": senders.KINDS, "transports": senders.TRANSPORTS, "formats": formats.FORMATS,
            "deliveries": delivery.status_for_bank(db, ctx.tenant_id), "describe": delivery.describe,
            "allow_udp": settings.siem_allow_private_hosts,
        }
    return {}


def _reminder_kwargs(db: Session, ctx: TenantContext, tenant: Tenant) -> dict:
    def safe(fn):
        try:
            return fn()
        except reminder_service.ReminderError:
            return []

    if ctx.customer_id is not None:
        customer = db.get(Customer, ctx.customer_id)
        stored = customer.reminder_offsets_minutes
        mode = "bank" if stored is None else "off" if stored == "" else "custom"
        inherited = reminder_service.describe(safe(lambda: reminder_service.bank_offsets(tenant)))
    else:
        stored = tenant.reminder_offsets_minutes
        mode = "default" if stored is None else "off" if stored == "" else "custom"
        inherited = reminder_service.describe(reminder_service.installation_offsets())
    offsets = safe(lambda: reminder_service.parse_offsets(stored)) if stored else []
    return {"reminder_mode": mode, "reminder_offsets": ", ".join(str(m) for m in offsets) or "120, 30",
            "reminder_inherited": inherited, "customer_level": ctx.customer_id is not None}


def render_section(request: Request, db: Session, ctx: TenantContext, key: str, *, error: str | None = None,
                   status_code: int = 200, **overrides) -> HTMLResponse:
    context = {**_section_context(db, ctx, key), **overrides}
    template = "settings/_section_body.html" if request.query_params.get("partial") else "settings/section.html"
    return render_template(request, template, ctx=ctx, key=key, title=SECTIONS[key], error=error,
                           status_code=status_code if not error or status_code != 200 else 422, **context)


def _section_route(key: str):
    def show(request: Request, db: Session = Depends(get_db), ctx: TenantContext = Depends(get_web_context)) -> HTMLResponse:
        _section_permission(ctx, key)
        return render_section(request, db, ctx, key)

    show.__name__ = f"settings_{key.replace('-', '_')}"
    return show


for _key in SECTIONS:
    router.add_api_route(f"/{_key}", _section_route(_key), methods=["GET"], response_class=HTMLResponse)


def _password_policy_kwargs(tenant: Tenant) -> dict:
    return {
        "password_min_length": tenant.password_min_length,
        "password_require_uppercase": tenant.password_require_uppercase,
        "password_require_lowercase": tenant.password_require_lowercase,
        "password_require_number": tenant.password_require_number,
        "password_require_symbol": tenant.password_require_symbol,
    }


def _outbox_kwargs(db: Session, tenant_id) -> dict:
    from openclear import plugins
    from openclear.services import decision_outbox_service, plugin_service

    outbox = decision_outbox_service.get_outbox(db, tenant_id)
    layouts = [(name, plugins.registry.decision_layouts[name]) for name in plugin_service.enabled(db, tenant_id, "decision_layouts")]
    return {"outbox": outbox, "outbox_layouts": layouts, "outbox_settings": outbox.layout_settings or {}}


def _record(db: Session, ctx: TenantContext, action: str, summary: str, resource_type: str = "tenant", resource_id=None) -> None:
    audit_log_service.record_action(db, ctx.tenant_id, actor_user_id=ctx.user_id, channel="web", action=action, summary=summary,
                                    resource_type=resource_type, resource_id=resource_id or ctx.tenant_id)


# --- Saving --------------------------------------------------------------------------------------


@router.post("")
@router.post("/branding")
async def update_settings(
    request: Request,
    name: str = Form(...),
    accent_color: str = Form(""),
    logo: UploadFile | None = File(None),
    favicon: UploadFile | None = File(None),
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("tenant:manage")),
    _csrf: None = Depends(verify_csrf),
) -> HTMLResponse:
    # An <input type="file"> left empty still submits a (filename-less) part in a
    # multipart form — that's "no new file", not "an empty file"; only treat it as a
    # replacement when a filename is actually present.
    logo_data = (logo.content_type or "application/octet-stream", await logo.read()) if logo and logo.filename else None
    favicon_data = (
        (favicon.content_type or "application/octet-stream", await favicon.read()) if favicon and favicon.filename else None
    )

    try:
        tenant = update_tenant_branding(
            db, ctx.tenant_id, name=name, accent_color=accent_color or None, logo=logo_data, favicon=favicon_data
        )
    except InvalidTenantSettingsInput as exc:
        db.rollback()
        return render_section(request, db, ctx, "branding", error=str(exc), tenant_display_name=name, accent_color=accent_color)

    if tenant is not None:
        _record(db, ctx, "tenant.update_settings", f"Updated organization settings (name: {tenant.name})")
    db.commit()
    return _saved(request, key="branding")


def _parse_optional_int(raw: str, *, unit: str) -> int | None:
    """Blank input means "use the global default" (None); anything else must be a
    plain integer — a non-numeric value is treated as invalid, same as a non-positive
    one, and both are reported through InvalidTenantSettingsInput."""
    raw = raw.strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        raise InvalidTenantSettingsInput(f"{raw!r} is not a whole number of {unit}") from None


@router.post("/session-timeout")
def update_session_timeout(
    request: Request,
    access_token_expire_minutes: str = Form(""),
    refresh_token_expire_minutes: str = Form(""),
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("tenant:manage")),
    _csrf: None = Depends(verify_csrf),
) -> HTMLResponse:
    try:
        access_minutes = _parse_optional_int(access_token_expire_minutes, unit="minutes")
        refresh_minutes = _parse_optional_int(refresh_token_expire_minutes, unit="minutes")
        tenant = set_session_timeouts(
            db, ctx.tenant_id, access_token_expire_minutes=access_minutes, refresh_token_expire_minutes=refresh_minutes
        )
    except InvalidTenantSettingsInput as exc:
        db.rollback()
        return render_section(request, db, ctx, "timeouts", error=str(exc))

    if tenant is not None:
        _record(db, ctx, "tenant.update_session_timeout", (
            f"Updated session timeout (access: {tenant.access_token_expire_minutes or 'default'} min, "
            f"refresh: {tenant.refresh_token_expire_minutes or 'default'} min)"))
    db.commit()
    return _saved(request, key="timeouts")


@router.post("/data-export-timeout")
def update_data_export_timeout(
    request: Request,
    timeout_seconds: str = Form(""),
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("tenant:manage")),
    _csrf: None = Depends(verify_csrf),
) -> HTMLResponse:
    try:
        parsed_timeout = _parse_optional_int(timeout_seconds, unit="seconds")
        tenant = set_data_export_timeout(db, ctx.tenant_id, timeout_seconds=parsed_timeout)
    except InvalidTenantSettingsInput as exc:
        db.rollback()
        return render_section(request, db, ctx, "timeouts", error=str(exc))

    if tenant is not None:
        _record(db, ctx, "tenant.update_data_export_timeout",
                f"Updated data export timeout ({tenant.data_export_timeout_seconds or 'default'} sec)")
    db.commit()
    return _saved(request, key="timeouts")


@router.post("/contact")
def update_contact_info(
    request: Request,
    support_email: str = Form(""),
    support_phone: str = Form(""),
    website: str = Form(""),
    address_line1: str = Form(""),
    address_line2: str = Form(""),
    city: str = Form(""),
    state: str = Form(""),
    postal_code: str = Form(""),
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("tenant:manage")),
    _csrf: None = Depends(verify_csrf),
) -> HTMLResponse:
    tenant = update_tenant_contact_info(
        db, ctx.tenant_id, support_email=support_email, support_phone=support_phone, website=website,
        address_line1=address_line1, address_line2=address_line2, city=city, state=state, postal_code=postal_code,
    )
    if tenant is not None:
        _record(db, ctx, "tenant.update_contact_info", "Updated organization contact info")
    db.commit()
    return _saved(request, key="contact")


@router.post("/decision-files")
async def update_decision_files(
    request: Request,
    enabled: bool = Form(False),
    layout: str = Form("openclear_csv"),
    interval_minutes: int = Form(60),
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("tenant:manage")),
    _csrf: None = Depends(verify_csrf),
) -> HTMLResponse:
    """The decision outbox (services/decision_outbox_service.py)."""
    from openclear.services import decision_outbox_service

    form = await request.form()
    prefix = f"setting_{layout}__"
    values = {key[len(prefix):]: str(value) for key, value in form.items() if key.startswith(prefix)}
    try:
        decision_outbox_service.save(db, ctx.tenant_id, enabled=enabled, layout=layout, interval_minutes=interval_minutes, settings=values)
    except decision_outbox_service.OutboxError as exc:
        db.rollback()
        return render_section(request, db, ctx, "decision-files", error=str(exc))
    _record(db, ctx, "tenant.update_decision_files", f"Decision files {'on' if enabled else 'off'}: {layout}, every {interval_minutes} minutes")
    db.commit()
    return _saved(request, key="decision-files")


@router.post("/embedding")
def update_embedding(
    request: Request,
    embed_allowed_origins: str = Form(""),
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("tenant:manage")),
    _csrf: None = Depends(verify_csrf),
) -> HTMLResponse:
    """Which online banking origins may frame OpenClear (services/embed_service.py)."""
    from openclear.services import embed_service

    try:
        origins = embed_service.set_allowed_origins(db, ctx.tenant_id, embed_allowed_origins)
    except embed_service.EmbedError as exc:
        db.rollback()
        return render_section(request, db, ctx, "embedding", error=str(exc), embed_allowed_origins=embed_allowed_origins)
    _record(db, ctx, "tenant.update_embedding", f"Embedding origins: {', '.join(origins) or 'none (embedding off)'}")
    db.commit()
    return _saved(request, key="embedding")


@router.post("/outside-services")
def review_outside_service(
    request: Request,
    service_key: str = Form(""),
    note: str = Form(""),
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("tenant:manage")),
    _csrf: None = Depends(verify_csrf),
) -> HTMLResponse:
    """The bank reviewed one of the outside services OpenClear uses for it (CONTROLS_PLAN.md CT3)."""
    from openclear.domain.user import User
    from openclear.services import outside_services

    try:
        service = outside_services.mark_reviewed(db, ctx.tenant_id, service_key, by=db.get(User, ctx.user_id).email, note=note)
    except ValueError as exc:
        db.rollback()
        return _saved(request, str(exc), key="outside-services")
    _record(db, ctx, "tenant.outside_service_reviewed", f"Reviewed the outside service {service.label}" + (f": {note.strip()[:200]}" if note.strip() else ""))
    db.commit()
    return _saved(request, f"Recorded your review of {service.label}.", key="outside-services")


@router.post("/standards")
def update_standards(
    request: Request,
    standard_password_min_length: str = Form(""),
    key_rotation_days: str = Form(""),
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("tenant:manage")),
    _csrf: None = Depends(verify_csrf),
) -> HTMLResponse:
    """The bank's own standards, which the password_policy and key_age checks hold OpenClear to."""
    from openclear.domain.tenant import Tenant

    def number(raw: str, low: int, high: int) -> int | None:
        if not raw.strip():
            return None
        value = int(raw)
        if not low <= value <= high:
            raise ValueError
        return value

    try:
        length, days = number(standard_password_min_length, 6, 128), number(key_rotation_days, 1, 3650)
    except ValueError:
        return _saved(request, "Use a minimum length of 6 to 128 and a rotation period of 1 to 3650 days, or leave them empty.", key="standards")
    tenant = db.get(Tenant, ctx.tenant_id)
    tenant.standard_password_min_length, tenant.key_rotation_days = length, days
    _record(db, ctx, "tenant.update_standards", f"Standards: passwords at least {length or 'unset'} characters; keys rotated every {days or 'unset'} days")
    db.commit()
    return _saved(request, key="standards")


@router.post("/customer-approval")
def update_customer_approval(
    request: Request,
    approve_customer_users: bool = Form(False),
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("tenant:manage")),
    _csrf: None = Depends(verify_csrf),
) -> HTMLResponse:
    """New people a customer's side adds wait for the bank's approval (MOBILE_PLAN.md R6)."""
    from openclear.domain.tenant import Tenant

    tenant = db.get(Tenant, ctx.tenant_id)
    if tenant.approve_customer_users != approve_customer_users:
        tenant.approve_customer_users = approve_customer_users
        _record(db, ctx, "tenant.update_customer_approval",
                f"New customer users {'wait for the bank' if approve_customer_users else 'no longer wait for the bank'} (customers' own settings still apply)")
    db.commit()
    return _saved(request, key="customer-approval")


@router.post("/dual-control")
def update_dual_control(
    request: Request,
    require_dual_control: bool = Form(False),
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("tenant:manage")),
    _csrf: None = Depends(verify_csrf),
) -> HTMLResponse:
    tenant = set_require_dual_control(db, ctx.tenant_id, require_dual_control)
    if tenant is not None:
        _record(db, ctx, "tenant.update_dual_control", f"{'Enabled' if require_dual_control else 'Disabled'} dual control")
    db.commit()
    return _saved(request, key="dual-control")


@router.post("/password-policy")
def update_password_policy(
    request: Request,
    min_length: int = Form(...),
    require_uppercase: bool = Form(False),
    require_lowercase: bool = Form(False),
    require_number: bool = Form(False),
    require_symbol: bool = Form(False),
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("tenant:manage")),
    _csrf: None = Depends(verify_csrf),
) -> HTMLResponse:
    try:
        tenant = set_password_policy(
            db, ctx.tenant_id, min_length=min_length, require_uppercase=require_uppercase,
            require_lowercase=require_lowercase, require_number=require_number, require_symbol=require_symbol,
        )
    except InvalidTenantSettingsInput as exc:
        db.rollback()
        return render_section(
            request, db, ctx, "password-policy", error=str(exc), password_min_length=min_length,
            password_require_uppercase=require_uppercase, password_require_lowercase=require_lowercase,
            password_require_number=require_number, password_require_symbol=require_symbol,
        )

    if tenant is not None:
        _record(db, ctx, "tenant.update_password_policy", f"Updated password policy (min length: {tenant.password_min_length})")
    db.commit()
    return _saved(request, key="password-policy")


@router.post("/messages")
def update_messages(
    request: Request,
    login_message: str = Form(""),
    banner_message: str = Form(""),
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("tenant:manage")),
    _csrf: None = Depends(verify_csrf),
) -> HTMLResponse:
    try:
        tenant = set_messages(db, ctx.tenant_id, login_message=login_message, banner_message=banner_message)
    except InvalidTenantSettingsInput as exc:
        db.rollback()
        return render_section(request, db, ctx, "messages", error=str(exc), login_message=login_message, banner_message=banner_message)

    if tenant is not None:
        _record(db, ctx, "tenant.update_messages", "Updated login/banner messages")
    db.commit()
    return _saved(request, key="messages")


@router.post("/reminders")
def update_reminders(
    request: Request,
    mode: str = Form(...),
    offsets: str = Form(""),
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(get_web_context),
    _csrf: None = Depends(verify_csrf),
) -> HTMLResponse:
    """The bank's reminder timing, or (for a customer's own users) the customer's."""
    _section_permission(ctx, "reminders")
    try:
        if ctx.customer_id is not None:
            customer = reminder_service.set_customer_reminders(db, ctx.tenant_id, ctx.customer_id, mode=mode, offsets=offsets)
            timing = reminder_service.describe(reminder_service.customer_offsets(db.get(Tenant, ctx.tenant_id), customer))
            _record(db, ctx, "customer.update_reminders", f"Reminders for {customer.name}: {timing}", "customer", customer.id)
        else:
            tenant = reminder_service.set_bank_reminders(db, ctx.tenant_id, mode=mode, offsets=offsets)
            _record(db, ctx, "tenant.update_reminders", f"Reminders: {reminder_service.describe(reminder_service.bank_offsets(tenant))}")
    except reminder_service.ReminderError as exc:
        db.rollback()
        return render_section(request, db, ctx, "reminders", error=str(exc), reminder_mode=mode, reminder_offsets=offsets)
    db.commit()
    return _saved(request, key="reminders")


@router.post("/security-events")
def update_security_events(
    request: Request,
    action: str = Form("save"),
    kind: str = Form(""),
    enabled: bool = Form(False),
    host: str = Form(""),
    port: str = Form(""),
    transport: str = Form("tls"),
    format: str = Form("json"),
    url: str = Form(""),
    token: str = Form(""),
    splunk_index: str = Form(""),
    splunk_sourcetype: str = Form(""),
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("tenant:manage")),
    _csrf: None = Depends(verify_csrf),
) -> HTMLResponse:
    """The bank's own SIEM destination (services/siem/delivery.py): save, test or remove."""
    if action == "remove":
        if delivery.remove_bank_destination(db, ctx.tenant_id):
            _record(db, ctx, "tenant.update_security_event_destination", "Stopped forwarding security events")
        db.commit()
        return _saved(request, "Security event forwarding removed.", key="security-events")
    try:
        destination = delivery.save_bank_destination(
            db, ctx.tenant_id, kind=kind, enabled=enabled, host=host, port=port, transport=transport, fmt=format, url=url,
            token=token, splunk_index=splunk_index, splunk_sourcetype=splunk_sourcetype)
    except delivery.DestinationError as exc:
        db.rollback()
        return render_section(request, db, ctx, "security-events", error=str(exc))
    _record(db, ctx, "tenant.update_security_event_destination",
            f"Security events {'forwarded' if enabled else 'paused'}: {delivery.describe(destination)}")
    db.commit()
    if action != "test":
        return _saved(request, key="security-events")
    try:
        from openclear.web import request_context

        current = request_context.current()
        delivery.send_test(destination, actor_label=current.user_email if current else None)
    except Exception as exc:  # noqa: BLE001 -- shown to the admin, who's testing exactly this
        message = str(exc) if isinstance(exc, senders.DeliveryError) else f"{type(exc).__name__}: {exc}"
        return render_section(request, db, ctx, "security-events", error=f"Saved, but the test event wasn't delivered: {message}")
    return _saved(request, "Saved. The test event was delivered.", key="security-events")


@router.get("/tile-states")
def tile_states(db: Session = Depends(get_db), ctx: TenantContext = Depends(require_panel)) -> JSONResponse:
    """Each tile's state, so the panel can update its tiles after a modal saves."""
    return JSONResponse({t.key: {"state": t.state, "tone": t.tone} for t in control_panel.tiles_for(db, ctx)})
