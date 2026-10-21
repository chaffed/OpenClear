# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Settings: the control panel (web/control_panel.py; UX_PLAN.md U11) and its small
settings. Each setting has its own URL, /ui/settings/<key>: a plain page, or with
?partial=1 just the form, which the panel shows in a modal (static/js/control-panel.js).
Saving redirects to the panel with a message; an error shows the setting again with it."""


import uuid

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy.orm import Session

from openclear.config import get_settings
from openclear.db.session import get_db
from openclear.db.tenancy import TenantContext
from openclear.domain.bulk_upload_file import BulkUploadFile, BulkUploadSource
from openclear.domain.customer import Customer
from openclear.domain.tenant import Tenant
from openclear.repositories.bulk_upload_file_repo import BulkUploadFileRepository
from openclear.services import audit_log_service, change_request_service, reminder_service
from openclear.services.siem import delivery, formats, senders
from openclear.services.tenant_service import (
    MAX_SECURITY_KEY_GRACE_DAYS,
    InvalidTenantSettingsInput,
    set_data_export_timeout,
    set_messages,
    set_password_policy,
    set_session_timeouts,
    update_tenant_branding,
    update_tenant_contact_info,
)
from openclear.web import control_panel
from openclear.web.deps import WebForbidden, get_web_context, render_template, require_web_permission
from openclear.web.redirects import see_other
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
    "security-keys": "Required security keys",
    "access-rules": "Lockout and inactive accounts",
    "timeouts": "Timeouts",
    "dual-control": "Dual control",
    "customer-approval": "New customer users",
    "outside-services": "Outside services",
    "standards": "Your standards",
    "reminders": "Reminders",
    "cutoff": "Decision cut-off",
    "embedding": "Embedding in online banking",
    "decision-files": "Decision files",
    "security-events": "Security event forwarding",
    "auto-import": "Auto-import",
}

# Each section's changes that can wait for a second person (DUAL_CONTROL_PLAN.md), shown on its form while one does.
SECTION_KINDS = {
    "password-policy": ("password_policy",), "security-keys": ("security_keys",), "access-rules": ("access_rules",), "dual-control": ("dual_control", "approval_switches"),
    "customer-approval": ("customer_approval",), "embedding": ("embedding",), "decision-files": ("decision_files",),
    "security-events": ("security_event_forwarding",), "cutoff": ("decision_cutoff",),
}


def _saved(request: Request, message: str = "Settings updated.", key: str | None = None):
    """Back to the panel with a message; the panel's script, which saves in the background,
    gets the message as JSON instead."""
    if request.headers.get("x-requested-with") == "fetch":
        return JSONResponse({"saved": True, "message": message, "key": key})
    return see_other("/ui/settings", flash=message, **{"from": key or None})


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
    if key == "security-keys":
        return {"require_security_key": tenant.require_security_key, "security_key_grace_days": tenant.security_key_grace_days,
                "security_key_required_since": tenant.security_key_required_since,
                "max_grace_days": MAX_SECURITY_KEY_GRACE_DAYS}
    if key == "access-rules":
        return _access_rules_kwargs(tenant)
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
        return {"require_dual_control": tenant.require_dual_control,
                "sensitive_changes_need_approval": tenant.sensitive_changes_need_approval,
                "access_changes_need_approval": tenant.access_changes_need_approval,
                "customer_changes_need_approval": tenant.customer_changes_need_approval}
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
    if key == "cutoff":
        return _cutoff_kwargs(db, tenant)
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


def _cutoff_kwargs(db: Session, tenant: Tenant, **typed) -> dict:
    from openclear.networks.registry import registered_codes
    from openclear.services import cutoff_service

    zones = cutoff_service.us_time_zones()
    return {"time_zone": tenant.time_zone or "", "cutoffs": dict(tenant.decision_cutoffs or {}),
            "min_review_minutes": tenant.cutoff_min_review_minutes, "networks": registered_codes(),
            "zones_first": zones, "zones_other": sorted(cutoff_service.available_time_zones() - set(zones)),
            "closures": cutoff_service.closures(db, tenant.id), **typed}


def render_section(request: Request, db: Session, ctx: TenantContext, key: str, *, error: str | None = None,
                   status_code: int = 200, **overrides) -> HTMLResponse:
    context = {**_section_context(db, ctx, key), **overrides}
    kinds = SECTION_KINDS.get(key, ())
    context.setdefault("waiting_changes", [c for c in change_request_service.pending(db, ctx.tenant_id, customer_id=ctx.customer_id,
                                                                                     all_customers=False) if c.kind in kinds] if kinds and
                       ctx.customer_id is None else [])
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


def _access_rules_kwargs(tenant: Tenant) -> dict:
    settings = get_settings()
    return {"max_failed_attempts": tenant.login_max_failed_attempts, "lockout_minutes": tenant.login_lockout_minutes,
            "lockout_until_unlocked": tenant.lockout_until_unlocked, "default_max_failed_attempts": settings.login_max_failed_attempts,
            "default_lockout_minutes": settings.login_lockout_minutes, "deactivate_inactive": tenant.deactivate_inactive,
            "deactivate_inactive_days": tenant.deactivate_inactive_days, "remove_inactive": tenant.remove_inactive,
            "remove_inactive_days": tenant.remove_inactive_days, "housekeeping": settings.enable_housekeeping_scheduler}


def _password_policy_kwargs(tenant: Tenant) -> dict:
    return {
        "password_min_length": tenant.password_min_length,
        "password_require_uppercase": tenant.password_require_uppercase,
        "password_require_lowercase": tenant.password_require_lowercase,
        "password_require_number": tenant.password_require_number,
        "password_require_symbol": tenant.password_require_symbol,
        "security_key_required": tenant.require_security_key,
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


def waiting_message(outcome) -> str:
    """What a person sees when their change went for approval (DUAL_CONTROL_PLAN.md)."""
    if outcome.change is not None and outcome.change.applies_at:
        return "Nobody else can approve this, so it takes effect in 24 hours unless you withdraw it (Settings → Changes awaiting approval)."
    return "Sent for approval: it takes effect when a second admin approves it (Settings → Changes awaiting approval)."


def _change(request: Request, db: Session, ctx: TenantContext, kind: str, payload: dict, *, key: str,
            record: tuple[str, str] | None = None, error_overrides: dict | None = None):
    """Make a setting change through dual control: applied at once (and recorded as `record`), or
    sent for approval. Returns the response."""

    try:
        outcome = change_request_service.request_or_apply(db, kind, payload, tenant_id=ctx.tenant_id, user_id=ctx.user_id)
    except (change_request_service.ChangeRequestError, ValueError) as exc:
        db.rollback()
        return render_section(request, db, ctx, key, error=str(exc), **(error_overrides or {}))
    if outcome.applied:
        if record and outcome.summary != "Nothing changed.":
            _record(db, ctx, *record)
        db.commit()
        return _saved(request, key=key)
    db.commit()
    return _saved(request, waiting_message(outcome), key=key)


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
    payload = {"enabled": enabled, "layout": layout, "interval_minutes": interval_minutes, "settings": values}
    savepoint = db.begin_nested()  # checked now, so a mistake shows at once rather than on approval
    try:
        decision_outbox_service.save(db, ctx.tenant_id, **payload)
    except decision_outbox_service.OutboxError as exc:
        savepoint.rollback()
        return render_section(request, db, ctx, "decision-files", error=str(exc))
    savepoint.rollback()
    return _change(request, db, ctx, "decision_files", payload, key="decision-files",
                   record=("tenant.update_decision_files", f"Decision files {'on' if enabled else 'off'}: {layout}, every {interval_minutes} minutes"))


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
        origins = embed_service.parse_origins(embed_allowed_origins)
    except embed_service.EmbedError as exc:
        return render_section(request, db, ctx, "embedding", error=str(exc), embed_allowed_origins=embed_allowed_origins)
    return _change(request, db, ctx, "embedding", {"text": embed_allowed_origins}, key="embedding",
                   record=("tenant.update_embedding", f"Embedding origins: {', '.join(origins) or 'none (embedding off)'}"),
                   error_overrides={"embed_allowed_origins": embed_allowed_origins})


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
    """New people a customer's side adds wait for the bank's approval (MOBILE_PLAN.md R6). Turning
    it off needs a second admin (DUAL_CONTROL_PLAN.md)."""
    return _change(request, db, ctx, "customer_approval", {"values": {"approve_customer_users": approve_customer_users}}, key="customer-approval",
                   record=("tenant.update_customer_approval",
                           f"New customer users {'wait for the bank' if approve_customer_users else 'no longer wait for the bank'} "
                           "(customers' own settings still apply)"))


@router.post("/dual-control")
def update_dual_control(
    request: Request,
    require_dual_control: bool = Form(False),
    sensitive_changes_need_approval: bool = Form(False),
    access_changes_need_approval: bool = Form(False),
    customer_changes_need_approval: bool = Form(False),
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("tenant:manage")),
    _csrf: None = Depends(verify_csrf),
) -> HTMLResponse:
    """Dual control on decisions, and the second admin for settings changes (DUAL_CONTROL_PLAN.md).
    Turning any on is refused while nobody else could approve (DC-D16); turning any off needs a
    second admin."""

    asked = {"require_dual_control": require_dual_control, "sensitive_changes_need_approval": sensitive_changes_need_approval,
             "access_changes_need_approval": access_changes_need_approval, "customer_changes_need_approval": customer_changes_need_approval}
    messages = []
    try:
        decisions = change_request_service.request_or_apply(db, "dual_control", {"values": {"require_dual_control": require_dual_control}},
                                                            tenant_id=ctx.tenant_id, user_id=ctx.user_id)
        if decisions.applied and decisions.summary != "Nothing changed.":
            _record(db, ctx, "tenant.update_dual_control", f"{'Enabled' if require_dual_control else 'Disabled'} dual control")
        elif decisions.waiting:
            messages.append(waiting_message(decisions))
        switches = change_request_service.request_or_apply(
            db, "approval_switches", {"values": {k: asked[k] for k in ("sensitive_changes_need_approval", "access_changes_need_approval",
                                                                        "customer_changes_need_approval")}},
            tenant_id=ctx.tenant_id, user_id=ctx.user_id)
        if switches.applied and switches.summary != "Nothing changed.":
            _record(db, ctx, "tenant.update_approval_switches", f"Second admin for changes: sensitive settings "
                    f"{'on' if sensitive_changes_need_approval else 'off'}, access changes {'on' if access_changes_need_approval else 'off'}, "
                    f"customers' changes {'on' if customer_changes_need_approval else 'off'}")
        elif switches.waiting:
            messages.append(waiting_message(switches))
    except (change_request_service.ChangeRequestError, ValueError) as exc:
        db.rollback()
        return render_section(request, db, ctx, "dual-control", error=str(exc), **asked)
    db.commit()
    return _saved(request, messages[0] if messages else "Settings updated.", key="dual-control")


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
    values = {"password_min_length": min_length, "password_require_uppercase": require_uppercase,
              "password_require_lowercase": require_lowercase, "password_require_number": require_number,
              "password_require_symbol": require_symbol}
    if min_length < 1:
        return render_section(request, db, ctx, "password-policy", error="Minimum password length must be at least 1", **values)
    return _change(request, db, ctx, "password_policy", {"values": values}, key="password-policy",
                   record=("tenant.update_password_policy", f"Updated password policy (min length: {min_length})"), error_overrides=values)


@router.post("/security-keys")
def update_security_keys(
    request: Request,
    require_security_key: bool = Form(False),
    security_key_grace_days: int = Form(14),
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("tenant:manage")),
    _csrf: None = Depends(verify_csrf),
) -> HTMLResponse:
    values = {"require_security_key": require_security_key, "security_key_grace_days": security_key_grace_days}
    return _change(request, db, ctx, "security_keys", {"values": values}, key="security-keys",
                   record=("tenant.update_security_keys",
                           f"Security keys {'required' if require_security_key else 'not required'} for password sign-ins "
                           f"(grace period {security_key_grace_days} days)"),
                   error_overrides={**values, "max_grace_days": MAX_SECURITY_KEY_GRACE_DAYS})


@router.post("/access-rules")
def update_access_rules(
    request: Request,
    max_failed_attempts: str = Form(""),
    lockout_minutes: str = Form(""),
    lockout_until_unlocked: bool = Form(False),
    deactivate_inactive: bool = Form(False),
    deactivate_inactive_days: int = Form(90),
    remove_inactive: bool = Form(False),
    remove_inactive_days: int = Form(365),
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("tenant:manage")),
    _csrf: None = Depends(verify_csrf),
) -> HTMLResponse:
    """Lockout after failed sign-ins, and deactivating and removing inactive access. A blank
    lockout number takes the installation's."""
    def number(text: str) -> int | None:
        return int(text) if text.strip() else None

    from openclear.services import change_request_service, inactivity_service

    try:
        values = {"login_max_failed_attempts": number(max_failed_attempts), "login_lockout_minutes": number(lockout_minutes),
                  "lockout_until_unlocked": lockout_until_unlocked, "deactivate_inactive": deactivate_inactive,
                  "deactivate_inactive_days": deactivate_inactive_days, "remove_inactive": remove_inactive,
                  "remove_inactive_days": remove_inactive_days}
        inactivity_service.validate(deactivate=deactivate_inactive, deactivate_days=deactivate_inactive_days, remove=remove_inactive,
                                    remove_days=remove_inactive_days)
        outcome = change_request_service.request_or_apply(db, "access_rules", {"values": values}, tenant_id=ctx.tenant_id, user_id=ctx.user_id)
    except (InvalidTenantSettingsInput, ValueError) as exc:
        db.rollback()
        settings = get_settings()
        return render_section(
            request, db, ctx, "access-rules", error=str(exc) if isinstance(exc, InvalidTenantSettingsInput)
            or not isinstance(exc, ValueError) or not str(exc).startswith("invalid literal") else "Give whole numbers.",
            max_failed_attempts=max_failed_attempts or None, lockout_minutes=lockout_minutes or None,
            lockout_until_unlocked=lockout_until_unlocked, default_max_failed_attempts=settings.login_max_failed_attempts,
            default_lockout_minutes=settings.login_lockout_minutes, deactivate_inactive=deactivate_inactive,
            deactivate_inactive_days=deactivate_inactive_days, remove_inactive=remove_inactive, remove_inactive_days=remove_inactive_days,
            housekeeping=settings.enable_housekeeping_scheduler)
    if not outcome.applied:
        db.commit()
        return _saved(request, waiting_message(outcome), key="access-rules")
    if outcome.summary != "Nothing changed.":
        _record(db, ctx, "tenant.update_access_rules", _access_rules_summary(db.get(Tenant, ctx.tenant_id)))
    db.commit()
    return _saved(request, key="access-rules")


def _access_rules_summary(tenant: Tenant) -> str:
    from openclear.auth.login_service import lockout_policy

    attempts, minutes, until_unlocked = lockout_policy(tenant)
    parts = [f"lock after {attempts} failed attempts " + ("until an administrator unlocks" if until_unlocked else f"for {minutes} minutes")]
    parts.append(f"deactivate after {tenant.deactivate_inactive_days} days inactive" if tenant.deactivate_inactive else "no automatic deactivation")
    parts.append(f"remove after {tenant.remove_inactive_days} days inactive" if tenant.remove_inactive else "no automatic removal")
    return "Sign-in and inactive accounts: " + "; ".join(parts)


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
    """The bank's own SIEM destination (services/siem/delivery.py): save, test or remove. Every
    change to where the bank's security events go needs a second admin (DUAL_CONTROL_PLAN.md); a
    test goes to the destination as entered, without saving it."""
    from openclear.auth.crypto import encrypt_secret

    if action == "remove":
        if delivery.bank_destination(db, ctx.tenant_id) is None:
            return _saved(request, "Security event forwarding removed.", key="security-events")
        return _change(request, db, ctx, "security_event_forwarding", {"remove": True}, key="security-events",
                       record=("tenant.update_security_event_destination", "Stopped forwarding security events"))
    fields = {"kind": kind, "enabled": enabled, "host": host, "port": port, "transport": transport, "fmt": format, "url": url,
              "splunk_index": splunk_index, "splunk_sourcetype": splunk_sourcetype}
    savepoint = db.begin_nested()
    try:
        proposed = delivery.save_bank_destination(db, ctx.tenant_id, token=token, **fields)
        described = delivery.describe(proposed)
        if action == "test":
            from openclear.web import request_context

            current = request_context.current()
            try:
                delivery.send_test(proposed, actor_label=current.user_email if current else None)
            except Exception as exc:  # noqa: BLE001 -- shown to the admin, who's testing exactly this
                message = str(exc) if isinstance(exc, senders.DeliveryError) else f"{type(exc).__name__}: {exc}"
                savepoint.rollback()
                return render_section(request, db, ctx, "security-events", error=f"The test event wasn't delivered: {message}")
    except delivery.DestinationError as exc:
        savepoint.rollback()
        return render_section(request, db, ctx, "security-events", error=str(exc))
    savepoint.rollback()
    if action == "test":
        return _saved(request, "The test event was delivered. Save to start forwarding there.", key="security-events")
    payload = {"fields": fields, "describe": described, "enabled": enabled, **({"token_encrypted": encrypt_secret(token)} if token else {})}
    return _change(request, db, ctx, "security_event_forwarding", payload, key="security-events",
                   record=("tenant.update_security_event_destination", f"Security events {'forwarded' if enabled else 'paused'}: {described}"))


@router.get("/tile-states")
def tile_states(db: Session = Depends(get_db), ctx: TenantContext = Depends(require_panel)) -> JSONResponse:
    """Each tile's state, so the panel can update its tiles after a modal saves."""
    return JSONResponse({t.key: {"state": t.state, "tone": t.tone} for t in control_panel.tiles_for(db, ctx)})


# --- The decision cut-off (UI_PLAN.md UI-D13) ----------------------------------------------------


@router.post("/cutoff")
async def update_cutoff(request: Request, db: Session = Depends(get_db), ctx: TenantContext = Depends(require_web_permission("tenant:manage")),
                        _csrf: None = Depends(verify_csrf)) -> HTMLResponse:
    """The bank's time zone, each network's cut-off and the minimum review time: a protected
    change, since it moves when decisions become final."""
    from openclear.networks.registry import registered_codes

    form = await request.form()
    payload = {"time_zone": str(form.get("time_zone") or ""), "min_review_minutes": str(form.get("min_review_minutes") or "0"),
               "cutoffs": {code: str(form.get(f"cutoff_{code}") or "") for code in registered_codes()}}
    typed = {"time_zone": payload["time_zone"], "cutoffs": payload["cutoffs"], "min_review_minutes": payload["min_review_minutes"]}
    return _change(request, db, ctx, "decision_cutoff", payload, key="cutoff",
                   record=("tenant.update_cutoffs", "Decision cut-off changed"), error_overrides=typed)


@router.post("/cutoff/closures")
def add_closure(request: Request, closed_on: str = Form(""), name: str = Form(""), db: Session = Depends(get_db),
                ctx: TenantContext = Depends(require_web_permission("tenant:manage")), _csrf: None = Depends(verify_csrf)) -> HTMLResponse:
    """A day the bank is closed: no cut-off falls on it."""
    from datetime import date

    from openclear.services import cutoff_service

    try:
        closure = cutoff_service.add_closure(db, ctx.tenant_id, date.fromisoformat(closed_on), name)
    except ValueError as exc:
        db.rollback()
        message = str(exc) if isinstance(exc, cutoff_service.CutoffError) else "Choose the day the bank is closed."
        return render_section(request, db, ctx, "cutoff", error=message)
    _record(db, ctx, "tenant.update_closures", f"Closed on {closure.closed_on.isoformat()}: {closure.name}")
    db.commit()
    return _saved(request, f"Closed on {closure.closed_on.isoformat()}: no cut-off that day.", key="cutoff")


@router.post("/cutoff/closures/{closure_id}/delete")
def remove_closure(request: Request, closure_id: uuid.UUID, db: Session = Depends(get_db),
                   ctx: TenantContext = Depends(require_web_permission("tenant:manage")), _csrf: None = Depends(verify_csrf)) -> HTMLResponse:
    from openclear.services import cutoff_service

    closure = cutoff_service.remove_closure(db, ctx.tenant_id, closure_id)
    if closure is not None:
        _record(db, ctx, "tenant.update_closures", f"No longer closed on {closure.closed_on.isoformat()} ({closure.name})")
    db.commit()
    return _saved(request, "Closure removed.", key="cutoff")
