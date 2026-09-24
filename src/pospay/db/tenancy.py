# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import uuid
from datetime import datetime
from dataclasses import dataclass

from sqlalchemy import event, text
from sqlalchemy.orm import Session


@dataclass(frozen=True, slots=True)
class TenantContext:
    """Identity of the authenticated caller, resolved once from the JWT by auth/deps.py
    and threaded explicitly through services/repositories. Never build this from a
    request body or path parameter — tenant_id must only ever come from the token.

    `permissions` is resolved fresh from the caller's SecurityGroup on every request (not
    baked into the JWT) — see auth/deps.py::decode_and_build_context — so permission
    changes and membership deactivation take effect on the very next request. The
    `tenant_*`/`has_*` branding fields are resolved the same way, from the same Tenant
    lookup that already happens there (see services/tenant_service.py::TenantBranding) —
    every template gets branding for free via `ctx`, no per-route plumbing needed.

    `customer_id` is None for a tenant-wide membership (bank-wide staff, the only kind
    that existed before customers did) and set for a customer-scoped one (bank staff
    assigned to just that client, or the client's own employee) — see
    domain/tenant_membership.py. When set, decode_and_build_context has already masked
    every tenant-admin-only permission out of `permissions` regardless of what the
    security group nominally contains; repositories/services still must be passed
    `customer_id` explicitly wherever they accept it (see CustomerScopedRepository) —
    this field alone doesn't filter anything by itself."""

    tenant_id: uuid.UUID
    user_id: uuid.UUID
    security_group_id: uuid.UUID
    permissions: frozenset[str]
    tenant_slug: str
    tenant_name: str
    accent_color: str | None
    has_logo: bool
    has_favicon: bool
    customer_id: uuid.UUID | None
    customer_name: str | None

    # Per-tenant session-length override (services/tenant_service.py::set_session_timeouts)
    # — None means "use config.Settings.jwt_access/refresh_token_expire_minutes", the same
    # value most tests that construct a TenantContext directly want, hence the default
    # (unlike every field above, which has none, deliberately, and must be passed
    # explicitly). Resolved the same way branding is, from the same Tenant lookup
    # decode_and_build_context already does — see auth/security.py::create_token's
    # access_token_expire_minutes/refresh_token_expire_minutes params.
    access_token_expire_minutes: int | None = None
    refresh_token_expire_minutes: int | None = None

    # Contact info for templates/base.html's footer (services/tenant_service.py::
    # TenantBranding carries the identical set pre-login) — defaulted to None for the
    # same reason as the two fields above: most tests that construct a TenantContext
    # directly don't care about these and shouldn't have to pass them.
    support_email: str | None = None
    support_phone: str | None = None
    website: str | None = None
    address_line1: str | None = None
    address_line2: str | None = None
    city: str | None = None
    state: str | None = None
    postal_code: str | None = None

    # Markdown source for templates/partials/_banner.html's persistent top-of-page
    # banner, rendered via web/templates.py::render_markdown. banner_message is the
    # tenant's own (every one of this tenant's users, bank-wide and customer-scoped
    # alike); customer_banner_message is only ever set for a customer-scoped session
    # (see auth/deps.py::decode_and_build_context) and is that customer's own — set by
    # the customer's own scoped users (customer_banner:manage, deliberately not masked
    # out of a customer-scoped session — see auth/permissions.py). If both are set, the
    # banner carousels: tenant message first, then the customer's.
    banner_message: str | None = None
    customer_banner_message: str | None = None

    # Mirrors User.must_change_password (an admin reset this user's password to a
    # temporary one). web/deps.py::get_web_context redirects every page but the
    # change-password form while it's set; auth/deps.py::get_current_context refuses API
    # use outright.
    must_change_password: bool = False

    # This is the shared public demo organization: web/demo_guard.py locks the handful of
    # actions that would let one visitor lock out or disrupt the others, and base.html
    # shows a demo notice.
    is_demo: bool = False

    # For base.html's "Approvals" nav badge: recommendations waiting on someone other than
    # their maker, in this session's scope. Only computed for sessions that can approve
    # (exception:decide), so it's 0 for everyone else.
    pending_approval_count: int = 0

    # The login session this request belongs to (auth/security.py::create_session_tokens):
    # `session_id` is what logout revokes; the two expiry times drive base.html's
    # keep-alive / idle-warning script and are what an organization switch or password
    # change carries forward when it re-mints tokens. None for tokens minted before
    # session claims existed.
    session_id: uuid.UUID | None = None
    access_expires_at: datetime | None = None
    session_expires_at: datetime | None = None


# --- Postgres row-level security --------------------------------------------------------
#
# The RLS policies (see migrations) only show rows whose tenant_id matches the
# transaction-local setting app.current_tenant_id. set_config(..., true) lasts only until
# the transaction ends, and requests commit part-way through (login, ingestion, exports),
# so the tenant is remembered on the Session and re-applied at the start of every
# transaction it begins. A no-op on SQLite and SQL Server. Primary isolation is still the
# repository filter (repositories/base.py); this is the second, independent layer.

_RLS_TENANT_KEY = "rls_tenant_id"


def _set_rls_tenant(connection, tenant_id: uuid.UUID) -> None:
    connection.execute(text("SELECT set_config('app.current_tenant_id', :tid, true)"), {"tid": str(tenant_id)})


def bind_session_to_tenant(session: Session, tenant_id: uuid.UUID) -> None:
    """From now on, every transaction on this session runs as `tenant_id` for RLS."""
    session.info[_RLS_TENANT_KEY] = tenant_id
    if session.get_bind().dialect.name == "postgresql":
        _set_rls_tenant(session.connection(), tenant_id)


@event.listens_for(Session, "after_begin")
def _reapply_rls_tenant(session: Session, _transaction, connection) -> None:
    tenant_id = session.info.get(_RLS_TENANT_KEY)
    if tenant_id is not None and connection.dialect.name == "postgresql":
        _set_rls_tenant(connection, tenant_id)
