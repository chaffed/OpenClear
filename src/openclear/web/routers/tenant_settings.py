# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from openclear.config import get_settings
from openclear.db.session import get_db
from openclear.db.tenancy import TenantContext
from openclear.domain.bulk_upload_file import BulkUploadFile, BulkUploadSource
from openclear.domain.tenant import Tenant
from openclear.repositories.bulk_upload_file_repo import BulkUploadFileRepository
from openclear.services import audit_log_service
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
from openclear.web.deps import render_template, require_web_permission
from openclear.web.security import verify_csrf

router = APIRouter(prefix="/ui/settings", tags=["web-tenant-settings"])


def _password_policy_kwargs(tenant: Tenant) -> dict:
    return {
        "password_min_length": tenant.password_min_length,
        "password_require_uppercase": tenant.password_require_uppercase,
        "password_require_lowercase": tenant.password_require_lowercase,
        "password_require_number": tenant.password_require_number,
        "password_require_symbol": tenant.password_require_symbol,
    }


@router.get("")
def settings_form(
    request: Request, db: Session = Depends(get_db), ctx: TenantContext = Depends(require_web_permission("tenant:manage"))
) -> HTMLResponse:
    tenant = db.get(Tenant, ctx.tenant_id)
    settings = get_settings()
    # Read-only visibility only — auto_import_* is a single process-wide config
    # (config.py), not a per-tenant database setting, so there's nothing to save here,
    # just the current config plus this tenant's own recent activity for it.
    recent_auto_imports = BulkUploadFileRepository(db, ctx.tenant_id).list(
        source=BulkUploadSource.AUTO_IMPORT, limit=5, order_by=BulkUploadFile.uploaded_at.desc()
    )
    return render_template(
        request, "settings/form.html", ctx=ctx, tenant_display_name=ctx.tenant_name, accent_color=ctx.accent_color or "",
        require_dual_control=tenant.require_dual_control,
        embed_allowed_origins=tenant.embed_allowed_origins or "",
        **_outbox_kwargs(db, ctx.tenant_id),
        access_token_expire_minutes=tenant.access_token_expire_minutes,
        refresh_token_expire_minutes=tenant.refresh_token_expire_minutes,
        default_access_token_expire_minutes=settings.jwt_access_token_expire_minutes,
        default_refresh_token_expire_minutes=settings.jwt_refresh_token_expire_minutes,
        data_export_timeout_seconds=tenant.data_export_timeout_seconds,
        default_data_export_timeout_seconds=settings.data_export_timeout_seconds,
        auto_import_enabled=settings.auto_import_enabled,
        auto_import_dropbox_dir=settings.auto_import_dropbox_dir,
        auto_import_interval_seconds=settings.auto_import_interval_seconds,
        recent_auto_imports=recent_auto_imports,
        support_email=tenant.support_email,
        support_phone=tenant.support_phone,
        website=tenant.website,
        address_line1=tenant.address_line1,
        address_line2=tenant.address_line2,
        city=tenant.city,
        state=tenant.state,
        postal_code=tenant.postal_code,
        login_message=tenant.login_message or "",
        banner_message=tenant.banner_message or "",
        **_password_policy_kwargs(tenant),
    )


@router.post("")
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
        return render_template(
            request,
            "settings/form.html",
            ctx=ctx,
            tenant_display_name=name,
            accent_color=accent_color,
            error=str(exc),
            status_code=422,
        )

    if tenant is not None:
        audit_log_service.record_action(
            db,
            ctx.tenant_id,
            actor_user_id=ctx.user_id,
            channel="web",
            action="tenant.update_settings",
            summary=f"Updated organization settings (name: {tenant.name})",
            resource_type="tenant",
            resource_id=ctx.tenant_id,
        )
    db.commit()
    return RedirectResponse("/ui/settings?flash=Settings+updated.", status_code=303)


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
        current = db.get(Tenant, ctx.tenant_id)
        settings = get_settings()
        return render_template(
            request,
            "settings/form.html",
            ctx=ctx,
            tenant_display_name=ctx.tenant_name,
            accent_color=ctx.accent_color or "",
            require_dual_control=current.require_dual_control,
            access_token_expire_minutes=current.access_token_expire_minutes,
            refresh_token_expire_minutes=current.refresh_token_expire_minutes,
            default_access_token_expire_minutes=settings.jwt_access_token_expire_minutes,
            default_refresh_token_expire_minutes=settings.jwt_refresh_token_expire_minutes,
            data_export_timeout_seconds=current.data_export_timeout_seconds,
            default_data_export_timeout_seconds=settings.data_export_timeout_seconds,
            error=str(exc),
            status_code=422,
            **_password_policy_kwargs(current),
        )

    if tenant is not None:
        audit_log_service.record_action(
            db,
            ctx.tenant_id,
            actor_user_id=ctx.user_id,
            channel="web",
            action="tenant.update_session_timeout",
            summary=(
                f"Updated session timeout (access: {tenant.access_token_expire_minutes or 'default'} min, "
                f"refresh: {tenant.refresh_token_expire_minutes or 'default'} min)"
            ),
            resource_type="tenant",
            resource_id=ctx.tenant_id,
        )
    db.commit()
    return RedirectResponse("/ui/settings?flash=Settings+updated.", status_code=303)


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
        current = db.get(Tenant, ctx.tenant_id)
        settings = get_settings()
        return render_template(
            request,
            "settings/form.html",
            ctx=ctx,
            tenant_display_name=ctx.tenant_name,
            accent_color=ctx.accent_color or "",
            require_dual_control=current.require_dual_control,
            access_token_expire_minutes=current.access_token_expire_minutes,
            refresh_token_expire_minutes=current.refresh_token_expire_minutes,
            default_access_token_expire_minutes=settings.jwt_access_token_expire_minutes,
            default_refresh_token_expire_minutes=settings.jwt_refresh_token_expire_minutes,
            data_export_timeout_seconds=current.data_export_timeout_seconds,
            default_data_export_timeout_seconds=settings.data_export_timeout_seconds,
            error=str(exc),
            status_code=422,
            **_password_policy_kwargs(current),
        )

    if tenant is not None:
        audit_log_service.record_action(
            db,
            ctx.tenant_id,
            actor_user_id=ctx.user_id,
            channel="web",
            action="tenant.update_data_export_timeout",
            summary=f"Updated data export timeout ({tenant.data_export_timeout_seconds or 'default'} sec)",
            resource_type="tenant",
            resource_id=ctx.tenant_id,
        )
    db.commit()
    return RedirectResponse("/ui/settings?flash=Settings+updated.", status_code=303)


@router.post("/contact")
def update_contact_info(
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
) -> RedirectResponse:
    tenant = update_tenant_contact_info(
        db, ctx.tenant_id, support_email=support_email, support_phone=support_phone, website=website,
        address_line1=address_line1, address_line2=address_line2, city=city, state=state, postal_code=postal_code,
    )
    if tenant is not None:
        audit_log_service.record_action(
            db,
            ctx.tenant_id,
            actor_user_id=ctx.user_id,
            channel="web",
            action="tenant.update_contact_info",
            summary="Updated organization contact info",
            resource_type="tenant",
            resource_id=ctx.tenant_id,
        )
    db.commit()
    return RedirectResponse("/ui/settings?flash=Settings+updated.", status_code=303)


def _outbox_kwargs(db: Session, tenant_id) -> dict:
    from openclear import plugins
    from openclear.services import decision_outbox_service, plugin_service

    outbox = decision_outbox_service.get_outbox(db, tenant_id)
    layouts = [(name, plugins.registry.decision_layouts[name]) for name in plugin_service.enabled(db, tenant_id, "decision_layouts")]
    return {"outbox": outbox, "outbox_layouts": layouts, "outbox_settings": outbox.layout_settings or {}}


@router.post("/decision-files")
async def update_decision_files(
    request: Request,
    enabled: bool = Form(False),
    layout: str = Form("openclear_csv"),
    interval_minutes: int = Form(60),
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("tenant:manage")),
    _csrf: None = Depends(verify_csrf),
) -> RedirectResponse:
    """The decision outbox (services/decision_outbox_service.py)."""
    from urllib.parse import quote

    from openclear.services import decision_outbox_service

    form = await request.form()
    prefix = f"setting_{layout}__"
    values = {key[len(prefix):]: str(value) for key, value in form.items() if key.startswith(prefix)}
    try:
        decision_outbox_service.save(db, ctx.tenant_id, enabled=enabled, layout=layout, interval_minutes=interval_minutes, settings=values)
    except decision_outbox_service.OutboxError as exc:
        db.rollback()
        return RedirectResponse(f"/ui/settings?error={quote(str(exc))}#decision-files", status_code=303)
    audit_log_service.record_action(
        db, ctx.tenant_id, actor_user_id=ctx.user_id, channel="web", action="tenant.update_decision_files",
        summary=f"Decision files {'on' if enabled else 'off'}: {layout}, every {interval_minutes} minutes",
        resource_type="tenant", resource_id=ctx.tenant_id,
    )
    db.commit()
    return RedirectResponse("/ui/settings?flash=Settings+updated.#decision-files", status_code=303)


@router.post("/embedding")
def update_embedding(
    embed_allowed_origins: str = Form(""),
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("tenant:manage")),
    _csrf: None = Depends(verify_csrf),
) -> RedirectResponse:
    """Which online banking origins may frame OpenClear (services/embed_service.py)."""
    from urllib.parse import quote

    from openclear.services import embed_service

    try:
        origins = embed_service.set_allowed_origins(db, ctx.tenant_id, embed_allowed_origins)
    except embed_service.EmbedError as exc:
        db.rollback()
        return RedirectResponse(f"/ui/settings?error={quote(str(exc))}#embedding", status_code=303)
    audit_log_service.record_action(
        db, ctx.tenant_id, actor_user_id=ctx.user_id, channel="web", action="tenant.update_embedding",
        summary=f"Embedding origins: {', '.join(origins) or 'none (embedding off)'}", resource_type="tenant", resource_id=ctx.tenant_id,
    )
    db.commit()
    return RedirectResponse("/ui/settings?flash=Settings+updated.#embedding", status_code=303)


@router.post("/dual-control")
def update_dual_control(
    require_dual_control: bool = Form(False),
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("tenant:manage")),
    _csrf: None = Depends(verify_csrf),
) -> RedirectResponse:
    tenant = set_require_dual_control(db, ctx.tenant_id, require_dual_control)
    if tenant is not None:
        audit_log_service.record_action(
            db,
            ctx.tenant_id,
            actor_user_id=ctx.user_id,
            channel="web",
            action="tenant.update_dual_control",
            summary=f"{'Enabled' if require_dual_control else 'Disabled'} dual control",
            resource_type="tenant",
            resource_id=ctx.tenant_id,
        )
    db.commit()
    return RedirectResponse("/ui/settings?flash=Settings+updated.", status_code=303)


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
        current = db.get(Tenant, ctx.tenant_id)
        settings = get_settings()
        return render_template(
            request,
            "settings/form.html",
            ctx=ctx,
            tenant_display_name=ctx.tenant_name,
            accent_color=ctx.accent_color or "",
            require_dual_control=current.require_dual_control,
            access_token_expire_minutes=current.access_token_expire_minutes,
            refresh_token_expire_minutes=current.refresh_token_expire_minutes,
            default_access_token_expire_minutes=settings.jwt_access_token_expire_minutes,
            default_refresh_token_expire_minutes=settings.jwt_refresh_token_expire_minutes,
            data_export_timeout_seconds=current.data_export_timeout_seconds,
            default_data_export_timeout_seconds=settings.data_export_timeout_seconds,
            error=str(exc),
            status_code=422,
            password_min_length=min_length,
            password_require_uppercase=require_uppercase,
            password_require_lowercase=require_lowercase,
            password_require_number=require_number,
            password_require_symbol=require_symbol,
        )

    if tenant is not None:
        audit_log_service.record_action(
            db,
            ctx.tenant_id,
            actor_user_id=ctx.user_id,
            channel="web",
            action="tenant.update_password_policy",
            summary=f"Updated password policy (min length: {tenant.password_min_length})",
            resource_type="tenant",
            resource_id=ctx.tenant_id,
        )
    db.commit()
    return RedirectResponse("/ui/settings?flash=Settings+updated.", status_code=303)


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
        current = db.get(Tenant, ctx.tenant_id)
        settings = get_settings()
        return render_template(
            request,
            "settings/form.html",
            ctx=ctx,
            tenant_display_name=ctx.tenant_name,
            accent_color=ctx.accent_color or "",
            require_dual_control=current.require_dual_control,
            access_token_expire_minutes=current.access_token_expire_minutes,
            refresh_token_expire_minutes=current.refresh_token_expire_minutes,
            default_access_token_expire_minutes=settings.jwt_access_token_expire_minutes,
            default_refresh_token_expire_minutes=settings.jwt_refresh_token_expire_minutes,
            data_export_timeout_seconds=current.data_export_timeout_seconds,
            default_data_export_timeout_seconds=settings.data_export_timeout_seconds,
            error=str(exc),
            status_code=422,
            login_message=login_message,
            banner_message=banner_message,
            **_password_policy_kwargs(current),
        )

    if tenant is not None:
        audit_log_service.record_action(
            db,
            ctx.tenant_id,
            actor_user_id=ctx.user_id,
            channel="web",
            action="tenant.update_messages",
            summary="Updated login/banner messages",
            resource_type="tenant",
            resource_id=ctx.tenant_id,
        )
    db.commit()
    return RedirectResponse("/ui/settings?flash=Settings+updated.", status_code=303)
