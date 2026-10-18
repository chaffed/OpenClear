# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import io
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from PIL import Image, UnidentifiedImageError
from sqlalchemy import select
from sqlalchemy.orm import Session

from openclear.config import get_settings
from openclear.domain.tenant import Tenant
from openclear.services import message_content
from openclear.web.branding_storage import save_tenant_asset

_HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
# Keyed by what Pillow detects from the file's own bytes, never by the browser-declared
# Content-Type (which the uploader controls). SVG is deliberately not accepted: it's a
# document that can carry script, and logos are served publicly from this app's own origin
# (web/routers/branding.py), which would make an SVG logo a stored-XSS vector.
_ALLOWED_IMAGE_FORMATS = {"PNG": "image/png", "JPEG": "image/jpeg", "ICO": "image/x-icon"}
# What web/routers/branding.py will serve. A logo stored before SVG was refused (or with
# an unrecognized type) counts as "no logo" until an admin uploads a new one; the file
# itself is left on disk, untouched.
SERVABLE_IMAGE_CONTENT_TYPES = frozenset(_ALLOWED_IMAGE_FORMATS.values()) | {"image/vnd.microsoft.icon"}


class InvalidTenantSettingsInput(ValueError):
    """Raised for any malformed tenant-settings form input — a bad accent color, a
    disallowed image content-type, or an invalid session timeout — callers turn this
    into a form error, never a 500."""


@dataclass(frozen=True, slots=True)
class TenantBranding:
    """What a template needs to render a tenant's branding — never the image bytes
    themselves (those are served separately, see web/routers/branding.py). `id` and
    `password_login_enabled` are here too (not just cosmetic fields) because the login
    routes need this same pre-authentication, slug-resolved lookup to know which tenant's
    SSO connections to offer and whether its password form should even be shown —
    see web/routers/auth.py."""

    id: uuid.UUID
    slug: str
    name: str
    accent_color: str | None
    has_logo: bool
    has_favicon: bool
    password_login_enabled: bool
    # The shared public demo organization (services/demo_tenant_service.py) — see
    # web/demo_guard.py for what's locked there.
    is_demo: bool
    access_token_expire_minutes: int | None
    refresh_token_expire_minutes: int | None
    # Contact info for templates/base.html's footer — see Tenant's own columns for the
    # full rationale. Carried on TenantBranding (not just TenantContext) so the footer
    # also renders pre-login, the same way accent_color/has_logo already do.
    support_email: str | None
    support_phone: str | None
    website: str | None
    address_line1: str | None
    address_line2: str | None
    city: str | None
    state: str | None
    postal_code: str | None
    # Markdown source, rendered via web/templates.py::render_markdown at display time —
    # see Tenant's own column comments. Carried here (not just TenantContext) so
    # login_message renders pre-login, the same reason contact info above is.
    login_message: str | None
    banner_message: str | None


def _branding_from_tenant(tenant: Tenant) -> TenantBranding:
    return TenantBranding(
        id=tenant.id,
        slug=tenant.slug,
        name=tenant.name,
        accent_color=tenant.accent_color,
        has_logo=bool(tenant.logo_path) and tenant.logo_content_type in SERVABLE_IMAGE_CONTENT_TYPES,
        has_favicon=bool(tenant.favicon_path) and tenant.favicon_content_type in SERVABLE_IMAGE_CONTENT_TYPES,
        password_login_enabled=tenant.password_login_enabled,
        is_demo=tenant.is_demo,
        access_token_expire_minutes=tenant.access_token_expire_minutes,
        refresh_token_expire_minutes=tenant.refresh_token_expire_minutes,
        support_email=tenant.support_email,
        support_phone=tenant.support_phone,
        website=tenant.website,
        address_line1=tenant.address_line1,
        address_line2=tenant.address_line2,
        city=tenant.city,
        state=tenant.state,
        postal_code=tenant.postal_code,
        login_message=tenant.login_message,
        banner_message=tenant.banner_message,
    )


def get_tenant_branding_by_id(session: Session, tenant_id: uuid.UUID) -> TenantBranding | None:
    """Used by auth/deps.py::decode_and_build_context to populate TenantContext for an
    already-authenticated request (tenant_id comes from the JWT)."""
    tenant = session.get(Tenant, tenant_id)
    if tenant is None:
        return None
    return _branding_from_tenant(tenant)


def get_tenant_branding_by_slug(session: Session, slug: str) -> TenantBranding | None:
    """Used by web/routers/auth.py's login routes to brand the login page BEFORE
    authentication — resolved by the slug in the URL/form, not a token. Returns None for
    an unknown or inactive slug so the caller can fall back to the generic, unbranded
    login form rather than leaking which slugs exist via an error."""
    tenant = session.execute(select(Tenant).where(Tenant.slug == slug)).scalar_one_or_none()
    if tenant is None or not tenant.is_active:
        return None
    return _branding_from_tenant(tenant)


_MIN_ACCENT_CONTRAST_RATIO = 4.5  # WCAG AA for text — every button renders white text on this color


def _relative_luminance(hex_color: str) -> float:
    """WCAG's standard relative-luminance formula (the same math the contrast-ratio
    formula in the WCAG 2.1 spec is built from) — used here purely to check a tenant's
    chosen accent color stays readable as white button text, in both light and dark mode
    (see static/css/app.css's button rule and the dark-mode section it links to)."""
    r, g, b = (int(hex_color[i : i + 2], 16) / 255 for i in (1, 3, 5))

    def _linearize(channel: float) -> float:
        return channel / 12.92 if channel <= 0.03928 else ((channel + 0.055) / 1.055) ** 2.4

    r, g, b = _linearize(r), _linearize(g), _linearize(b)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _contrast_ratio_against_white(hex_color: str) -> float:
    # White's own relative luminance is exactly 1.0 — the WCAG contrast-ratio formula is
    # (lighter + 0.05) / (darker + 0.05), and white is always the lighter of the two here.
    return (1.0 + 0.05) / (_relative_luminance(hex_color) + 0.05)


def _validate_accent_color(accent_color: str | None) -> str | None:
    if not accent_color:
        return None
    if not _HEX_COLOR_RE.match(accent_color):
        raise InvalidTenantSettingsInput(f"{accent_color!r} is not a valid hex color — expected e.g. #2563eb")
    if _contrast_ratio_against_white(accent_color) < _MIN_ACCENT_CONTRAST_RATIO:
        raise InvalidTenantSettingsInput(
            f"{accent_color!r} is too light to read as white button text — try a darker shade "
            f"(needs at least a {_MIN_ACCENT_CONTRAST_RATIO}:1 contrast ratio against white)"
        )
    return accent_color


def _detect_image_type(data: bytes) -> str:
    """The real content type of an uploaded logo/favicon, from its bytes. Raises
    InvalidTenantSettingsInput for anything that isn't a PNG, JPEG, or ICO image."""
    try:
        with Image.open(io.BytesIO(data)) as image:
            detected = image.format
            image.verify()
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, SyntaxError, ValueError):
        detected = None
    if detected not in _ALLOWED_IMAGE_FORMATS:
        raise InvalidTenantSettingsInput("Upload a PNG, JPEG, or ICO image. SVG and other formats aren't accepted.")
    return _ALLOWED_IMAGE_FORMATS[detected]


def update_tenant_branding(
    session: Session,
    tenant_id: uuid.UUID,
    *,
    name: str,
    accent_color: str | None,
    logo: tuple[str, bytes] | None = None,
    favicon: tuple[str, bytes] | None = None,
) -> Tenant | None:
    """logo/favicon are (content_type, bytes) tuples, passed only when the admin actually
    picked a new file — omitting either leaves the existing one untouched, so re-saving
    the form just to change the color doesn't clobber an already-uploaded logo. Raises
    InvalidTenantSettingsInput on a malformed color or a disallowed image type; the route turns
    that into a form error rather than a 500."""
    tenant = session.get(Tenant, tenant_id)
    if tenant is None:
        return None

    tenant.name = name
    tenant.accent_color = _validate_accent_color(accent_color)

    if logo is not None:
        _declared_type, data = logo
        content_type = _detect_image_type(data)
        tenant.logo_path = save_tenant_asset(tenant_id, "logo", content_type, data)
        tenant.logo_content_type = content_type

    if favicon is not None:
        _declared_type, data = favicon
        content_type = _detect_image_type(data)
        tenant.favicon_path = save_tenant_asset(tenant_id, "favicon", content_type, data)
        tenant.favicon_content_type = content_type

    session.flush()
    return tenant


def set_require_dual_control(session: Session, tenant_id: uuid.UUID, enabled: bool) -> Tenant | None:
    """The only way to change this after initial provisioning — `require_dual_control`
    was previously set-once, at tenant creation (services/provisioning_service.py), with
    no self-service toggle anywhere in the UI."""
    tenant = session.get(Tenant, tenant_id)
    if tenant is None:
        return None
    tenant.require_dual_control = enabled
    session.flush()
    return tenant


def set_session_timeouts(
    session: Session,
    tenant_id: uuid.UUID,
    *,
    access_token_expire_minutes: int | None,
    refresh_token_expire_minutes: int | None,
) -> Tenant | None:
    """None means "use config.Settings.jwt_access/refresh_token_expire_minutes" (the
    app-wide default) — see auth/security.py::create_token, which every login/refresh/
    switch-tenant/WebAuthn-verify call site threads this through to. Raises
    InvalidTenantSettingsInput if either value is given but isn't a positive integer."""
    for label, value in (
        ("Idle sign-out", access_token_expire_minutes),
        ("Maximum session length", refresh_token_expire_minutes),
    ):
        if value is not None and value <= 0:
            raise InvalidTenantSettingsInput(f"{label} must be a positive number of minutes")
    # The access token's lifetime is the idle timeout and the refresh token's is the
    # maximum session length (auth/security.py::create_session_tokens caps one at the
    # other), so an idle timeout longer than the maximum would silently never apply.
    settings = get_settings()
    effective_idle = access_token_expire_minutes or settings.jwt_access_token_expire_minutes
    effective_max = refresh_token_expire_minutes or settings.jwt_refresh_token_expire_minutes
    if effective_idle > effective_max:
        raise InvalidTenantSettingsInput(
            f"Idle sign-out ({effective_idle} minutes) can't be longer than the maximum session length "
            f"({effective_max} minutes)."
        )

    tenant = session.get(Tenant, tenant_id)
    if tenant is None:
        return None
    tenant.access_token_expire_minutes = access_token_expire_minutes
    tenant.refresh_token_expire_minutes = refresh_token_expire_minutes
    session.flush()
    return tenant


def set_data_export_timeout(session: Session, tenant_id: uuid.UUID, *, timeout_seconds: int | None) -> Tenant | None:
    """None means "use config.Settings.data_export_timeout_seconds" (the app-wide
    default) — see services/data_export_service.py::run_export_job, which resolves this
    before starting the background archive-building job. Raises
    InvalidTenantSettingsInput if given but not a positive integer."""
    if timeout_seconds is not None and timeout_seconds <= 0:
        raise InvalidTenantSettingsInput("Data export timeout must be a positive number of seconds")

    tenant = session.get(Tenant, tenant_id)
    if tenant is None:
        return None
    tenant.data_export_timeout_seconds = timeout_seconds
    session.flush()
    return tenant


MAX_SECURITY_KEY_GRACE_DAYS = 90


def set_security_key_requirement(session: Session, tenant_id: uuid.UUID, *, required: bool, grace_days: int) -> Tenant | None:
    """Require a security key for password sign-ins (OWASP_PLAN.md OW-D18, OW-D20). Turning it on
    starts the grace period now; changing only the grace days keeps the start."""
    if not 0 <= grace_days <= MAX_SECURITY_KEY_GRACE_DAYS:
        raise InvalidTenantSettingsInput(f"The grace period must be between 0 and {MAX_SECURITY_KEY_GRACE_DAYS} days")
    tenant = session.get(Tenant, tenant_id)
    if tenant is None:
        return None
    if required and not tenant.require_security_key:
        tenant.security_key_required_since = datetime.now(timezone.utc)
    if not required:
        tenant.security_key_required_since = None
    tenant.require_security_key = required
    tenant.security_key_grace_days = grace_days
    session.flush()
    return tenant


def set_password_policy(
    session: Session,
    tenant_id: uuid.UUID,
    *,
    min_length: int,
    require_uppercase: bool,
    require_lowercase: bool,
    require_number: bool,
    require_symbol: bool,
) -> Tenant | None:
    """The organization's baseline password policy, checked once at account-creation
    time (see auth/password_policy.py) — never retroactively against an existing user's
    password, since there's no password-change flow to re-check anyway. A customer can
    only add to this (services/customer_service.py::set_password_policy), never weaken
    it. Raises InvalidTenantSettingsInput for a non-positive minimum length."""
    if min_length < 1:
        raise InvalidTenantSettingsInput("Minimum password length must be at least 1")

    tenant = session.get(Tenant, tenant_id)
    if tenant is None:
        return None
    tenant.password_min_length = min_length
    tenant.password_require_uppercase = require_uppercase
    tenant.password_require_lowercase = require_lowercase
    tenant.password_require_number = require_number
    tenant.password_require_symbol = require_symbol
    session.flush()
    return tenant


def set_access_rules(
    session: Session,
    tenant_id: uuid.UUID,
    *,
    max_failed_attempts: int | None,
    lockout_minutes: int | None,
    lockout_until_unlocked: bool,
    deactivate_inactive: bool,
    deactivate_inactive_days: int,
    remove_inactive: bool,
    remove_inactive_days: int,
) -> Tenant | None:
    """The bank's lockout after failed sign-ins (None: the installation's), and its rules for
    deactivating and removing inactive access (services/inactivity_service.py). Raises
    InvalidTenantSettingsInput for numbers out of range."""
    from openclear.services.inactivity_service import InactivityError, validate

    if max_failed_attempts is not None and not 3 <= max_failed_attempts <= 20:
        raise InvalidTenantSettingsInput("Lock after between 3 and 20 failed attempts.")
    if lockout_minutes is not None and not 1 <= lockout_minutes <= 1440:
        raise InvalidTenantSettingsInput("Lock for between 1 minute and a day.")
    try:
        validate(deactivate=deactivate_inactive, deactivate_days=deactivate_inactive_days, remove=remove_inactive,
                 remove_days=remove_inactive_days)
    except InactivityError as exc:
        raise InvalidTenantSettingsInput(str(exc)) from None
    tenant = session.get(Tenant, tenant_id)
    if tenant is None:
        return None
    tenant.login_max_failed_attempts, tenant.login_lockout_minutes = max_failed_attempts, lockout_minutes
    tenant.lockout_until_unlocked = lockout_until_unlocked
    tenant.deactivate_inactive, tenant.deactivate_inactive_days = deactivate_inactive, deactivate_inactive_days
    tenant.remove_inactive, tenant.remove_inactive_days = remove_inactive, remove_inactive_days
    session.flush()
    return tenant


def set_messages(
    session: Session, tenant_id: uuid.UUID, *, login_message: str | None, banner_message: str | None
) -> Tenant | None:
    """Markdown source for the pre-login notice (login_message) and the persistent
    post-login banner (banner_message) — see Tenant's own column comments and
    web/templates.py::render_markdown, which does the actual HTML conversion/sanitization
    at display time, not here. Blank input normalizes to None (nothing renders) rather
    than storing an empty string. Raises InvalidTenantSettingsInput (via
    services/message_content.py's validation, which also validates/re-encodes any
    embedded image) if either is too long or contains an invalid/oversized image."""
    try:
        login_message = message_content.validate_and_normalize_message(login_message)
        banner_message = message_content.validate_and_normalize_message(banner_message)
    except message_content.InvalidMessageContent as exc:
        raise InvalidTenantSettingsInput(str(exc)) from exc

    tenant = session.get(Tenant, tenant_id)
    if tenant is None:
        return None
    tenant.login_message = login_message
    tenant.banner_message = banner_message
    session.flush()
    return tenant


def update_tenant_contact_info(
    session: Session,
    tenant_id: uuid.UUID,
    *,
    support_email: str | None,
    support_phone: str | None,
    website: str | None,
    address_line1: str | None,
    address_line2: str | None,
    city: str | None,
    state: str | None,
    postal_code: str | None,
) -> Tenant | None:
    """Free-text, no format validation — same as Customer's identical contact fields.
    All-None clears every field, which is also what makes templates/base.html's footer
    stop rendering (see TenantBranding)."""
    tenant = session.get(Tenant, tenant_id)
    if tenant is None:
        return None
    tenant.support_email = support_email or None
    tenant.support_phone = support_phone or None
    tenant.website = website or None
    tenant.address_line1 = address_line1 or None
    tenant.address_line2 = address_line2 or None
    tenant.city = city or None
    tenant.state = state or None
    tenant.postal_code = postal_code or None
    session.flush()
    return tenant
