# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
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
    # Per-account access (db/account_scope.py): the accounts a customer's person is limited
    # to, or None when they see every account their customer scope allows.
    account_ids: frozenset[uuid.UUID] | None = None
    # Signed in through a customer's own sign-on: the session can't switch to another
    # organization (CUSTOMER_ADMIN_PLAN.md, guardrail 1; web/routers/tenant_switch.py).
    confined: bool = False
    # How the session was signed in ("password", "sso"; None for a token minted before
    # 1.21.2), from the token's `am` claim (auth/security.py::create_token).
    signed_in_with: str | None = None
    # When the person last authenticated (epoch seconds; the token's `auth` claim), or None.
    authenticated_at: int | None = None
    # While a required security key's grace period runs and this person has none: when it ends
    # (auth/deps.py::_security_key_due), for base.html's banner.
    security_key_due: datetime | None = None

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

    # An embedded session (services/embed_service.py): the online banking origin that frames
    # it. Templates drop the navigation, and the pages may be framed by that origin only.
    embed_parent_origin: str | None = None

    # A support session (services/support_access_service.py): the bank's grant it runs
    # under, until when, and whether it can write. base.html shows a banner throughout.
    support_grant_id: uuid.UUID | None = None
    support_expires_at: datetime | None = None
    support_write: bool = False

    # Mirrors User.must_change_password (an admin reset this user's password to a
    # temporary one). web/deps.py::get_web_context redirects every page but the
    # change-password form while it's set; auth/deps.py::get_current_context refuses API
    # use outright.
    must_change_password: bool = False
    # Who's signed in, for the account menu (UI_PLAN.md UI-D6), and how many organizations they
    # belong to besides this one (offers "Switch organization" only when there's somewhere to go).
    user_email: str | None = None
    other_organizations: int = 0

    # This is the shared public demo organization: web/demo_guard.py locks the handful of
    # actions that would let one visitor lock out or disrupt the others, and base.html
    # shows a demo notice.
    is_demo: bool = False

    # For base.html's "Approvals" nav badge: recommendations waiting on someone other than
    # their maker, in this session's scope. Only computed for sessions that can approve
    # (exception:decide), so it's 0 for everyone else.
    pending_approval_count: int = 0
    # For base.html's "Exceptions" nav badge: exceptions needing a decision (open or
    # awaiting approval), in this session's scope. Web pages only (web/deps.py), and only
    # for those who can see exceptions.
    needs_attention_count: int = 0
    # The soonest decision deadline among them (base.html's "decide now" banner).
    nearest_deadline: datetime | None = None
    # For base.html's "New customer users" nav entry: people a customer's side added who wait
    # for the bank's approval (MOBILE_PLAN.md R6). Web pages only, for the bank's staff who can
    # manage customers.
    customer_users_waiting: int = 0

    # The login session this request belongs to (auth/security.py::create_session_tokens):
    # `session_id` is what logout revokes; the two expiry times drive base.html's
    # keep-alive / idle-warning script and are what an organization switch or password
    # change carries forward when it re-mints tokens. None for tokens minted before
    # session claims existed.
    session_id: uuid.UUID | None = None
    access_expires_at: datetime | None = None
    session_expires_at: datetime | None = None

    # Set when an API client is acting for this user (auth/deps.py::_act_as, SDK_PLAN.md
    # P0): the request runs as the user, with only the permissions both have.
    acting_api_client_id: uuid.UUID | None = None


# --- Postgres row-level security --------------------------------------------------------
#
# The RLS policies (see migrations) only show rows whose tenant_id matches the
# transaction-local setting app.current_tenant_id; tenant_membership also shows the
# signed-in user's own rows in other banks (app.current_user_id), for switching
# organization. set_config(..., true) lasts only until the transaction ends, and requests
# commit part-way through (login, ingestion, exports), so the binding is remembered on the
# Session and re-applied at the start of every transaction it begins. A no-op on SQLite and
# SQL Server. Primary isolation is still the repository filter (repositories/base.py); this
# is the second, independent layer.
#
# Code with no signed-in user (scheduled jobs, the platform operator API, provisioning)
# binds each unit of work to its bank as it starts it.

_RLS_TENANT_KEY = "rls_tenant_id"
_RLS_USER_KEY = "rls_user_id"


def _apply_rls(connection, tenant_id: uuid.UUID | None, user_id: uuid.UUID | None) -> None:
    connection.execute(
        text("SELECT set_config('app.current_tenant_id', :tid, true), set_config('app.current_user_id', :uid, true)"),
        {"tid": str(tenant_id) if tenant_id else "", "uid": str(user_id) if user_id else ""},
    )


def bind_session_to_tenant(session: Session, tenant_id: uuid.UUID, *, user_id: uuid.UUID | None = None) -> None:
    """From now on, every transaction on this session runs as `tenant_id` (and, if
    given, the signed-in `user_id`) for RLS."""
    session.info[_RLS_TENANT_KEY] = tenant_id
    session.info[_RLS_USER_KEY] = user_id
    if session.get_bind().dialect.name == "postgresql":
        _apply_rls(session.connection(), tenant_id, user_id)


def memberships_elsewhere(session: Session, user_id: uuid.UUID, tenant_id: uuid.UUID, *, customer_id: uuid.UUID | None = None,
                          within_customer: bool = False) -> int:
    """How many of this person's memberships, active or not, are outside this bank (or,
    with `within_customer`, outside this one customer of it, the bank's own staff
    memberships included). For admin actions that reach a person's whole identity, such as
    a password reset, which mustn't touch anyone who belongs somewhere else.

    Under Postgres row-level security an admin's session sees only its own bank's
    memberships, which would make every person look like they belong nowhere else. For this
    one query the session names the person (`app.membership_probe_user`), which the
    tenant_membership policy lets it see across banks (migration c6e8a0c2e4f7); only the
    count leaves this function."""
    from sqlalchemy import func, or_, select

    from openclear.domain.tenant_membership import TenantMembership

    # Spelled out rather than NOT(...): a bank-wide membership's customer_id is NULL, and
    # NOT (customer_id = x) is NULL for it, which would leave the bank's own staff uncounted.
    if not within_customer:
        elsewhere = TenantMembership.tenant_id != tenant_id
    elif customer_id is None:
        elsewhere = or_(TenantMembership.tenant_id != tenant_id, TenantMembership.customer_id.is_not(None))
    else:
        elsewhere = or_(TenantMembership.tenant_id != tenant_id, TenantMembership.customer_id.is_(None),
                        TenantMembership.customer_id != customer_id)
    postgres = session.get_bind().dialect.name == "postgresql"
    if postgres:
        session.execute(text("SELECT set_config('app.membership_probe_user', :uid, true)"), {"uid": str(user_id)})
    try:
        return session.execute(select(func.count()).select_from(TenantMembership).where(
            TenantMembership.user_id == user_id, elsewhere)).scalar_one()
    finally:
        if postgres:
            session.execute(text("SELECT set_config('app.membership_probe_user', '', true)"))


@contextmanager
def acting_as_tenant(session: Session, tenant_id: uuid.UUID) -> Iterator[None]:
    """Temporarily run as another bank, keeping the signed-in user, then restore the
    previous binding. For reads a signed-in user is entitled to in a bank they're a member
    of (switching organization)."""
    previous_tenant, previous_user = session.info.get(_RLS_TENANT_KEY), session.info.get(_RLS_USER_KEY)
    bind_session_to_tenant(session, tenant_id, user_id=previous_user)
    try:
        yield
    finally:
        session.info[_RLS_TENANT_KEY], session.info[_RLS_USER_KEY] = previous_tenant, previous_user
        if session.get_bind().dialect.name == "postgresql" and session.in_transaction():
            _apply_rls(session.connection(), previous_tenant, previous_user)


@event.listens_for(Session, "after_begin")
def _reapply_rls(session: Session, _transaction, connection) -> None:
    tenant_id, user_id = session.info.get(_RLS_TENANT_KEY), session.info.get(_RLS_USER_KEY)
    if (tenant_id is not None or user_id is not None) and connection.dialect.name == "postgresql":
        _apply_rls(connection, tenant_id, user_id)
