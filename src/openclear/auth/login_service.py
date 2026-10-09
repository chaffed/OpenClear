# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import enum
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from openclear.db.tenancy import bind_session_to_tenant
from openclear.auth.security import verify_password
from openclear.auth.webauthn_service import user_has_webauthn_credentials
from openclear.config import Settings, get_settings
from openclear.domain.customer import Customer
from openclear.domain.tenant import Tenant
from openclear.domain.tenant_membership import TenantMembership
from openclear.domain.user import User
from openclear.services import notification_service


def _as_utc(dt: datetime) -> datetime:
    # SQLite drops tzinfo on reload (same caveat audit_log_service.py::_normalize_datetime
    # and ml/train.py::_seconds_since document) — a naive value here is always
    # already-UTC by construction (see User.locked_until).
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


@dataclass(frozen=True, slots=True)
class AuthenticatedIdentity:
    user: User
    tenant: Tenant
    membership: TenantMembership
    mfa_required: bool
    # True only when membership.require_webauthn is set and this identity has no
    # WebAuthn credential registered in this tenant yet — the caller must route to a
    # forced-registration ceremony instead of the ordinary challenge page (see
    # web/routers/auth.py's /login/webauthn/setup* routes).
    needs_webauthn_enrollment: bool = False


class PasswordLoginOutcome(str, enum.Enum):
    SUCCESS = "success"
    # Unknown tenant/user, wrong password, or no active membership at all — collapsed
    # into one outcome so neither caller is tempted into a response that reveals which
    # part was wrong.
    INVALID_CREDENTIALS = "invalid_credentials"
    # The password was CORRECT, but every membership this identity holds in this tenant
    # is in a scope (the tenant itself, or a specific customer) that currently requires
    # SSO — see Tenant.password_login_enabled / Customer.password_login_enabled and
    # services/sso_service.py's lockout-guarded toggle for that setting.
    SSO_REQUIRED = "sso_required"
    # Threshold reached (config.Settings.login_max_failed_attempts) — locked until
    # User.locked_until, regardless of whether THIS attempt's password was even correct;
    # a correct password on an already-locked account still must not succeed, or the
    # lockout would be pointless once an attacker's Nth guess happens to land.
    LOCKED = "locked"


@dataclass(frozen=True, slots=True)
class PasswordLoginResult:
    outcome: PasswordLoginOutcome
    identity: AuthenticatedIdentity | None = None


def authenticate_password(
    session: Session, tenant_slug: str, email: str, password: str, *, settings: Settings | None = None
) -> PasswordLoginResult:
    """Shared by the JSON API (api/v1/auth.py) and the web login form
    (web/routers/auth.py) so credential-checking logic exists in exactly one place.

    `email` resolves a User globally now (see domain/user.py) rather than within this one
    tenant — the same identity can hold a TenantMembership (and therefore log in) in more
    than one tenant, each with its own security group.

    Account lockout is tracked on User (global to the identity, not per-tenant — a brute
    force targets this one password regardless of which tenant the attempt is against;
    see domain/user.py). This function only flushes, never commits — callers
    (web/routers/auth.py::login_submit, api/v1/auth.py::login) must each commit right
    after calling this, or a failed attempt's counter increment is silently lost when the
    request's session closes."""
    settings = settings or get_settings()
    tenant = session.execute(select(Tenant).where(Tenant.slug == tenant_slug)).scalar_one_or_none()
    if tenant is None or not tenant.is_active:
        return PasswordLoginResult(PasswordLoginOutcome.INVALID_CREDENTIALS)
    # The rest of login reads RLS-protected tables (memberships, security groups, keys),
    # so the session runs as this bank from here on (a no-op off Postgres).
    bind_session_to_tenant(session, tenant.id)

    user = session.execute(select(User).where(User.email == email)).scalar_one_or_none()
    if user is None or not user.is_active or user.is_service_account:
        return PasswordLoginResult(PasswordLoginOutcome.INVALID_CREDENTIALS)

    if user.locked_until is not None and _as_utc(user.locked_until) > datetime.now(timezone.utc):
        return PasswordLoginResult(PasswordLoginOutcome.LOCKED)

    if not verify_password(password, user.hashed_password):
        user.failed_login_attempts += 1
        if user.failed_login_attempts >= settings.login_max_failed_attempts:
            user.locked_until = datetime.now(timezone.utc) + timedelta(minutes=settings.login_lockout_minutes)
            session.flush()
            # Only fires the moment the lockout threshold is actually crossed, not on
            # every subsequent rejected attempt while already locked (that's the
            # early-return at the top of this function, above).
            notification_service.notify_account_locked(session, user)
            return PasswordLoginResult(PasswordLoginOutcome.LOCKED)
        session.flush()
        return PasswordLoginResult(PasswordLoginOutcome.INVALID_CREDENTIALS)

    # Correct password proves the brute force didn't work, regardless of whether the
    # login ultimately succeeds or hits SSO_REQUIRED below.
    if user.failed_login_attempts or user.locked_until is not None:
        user.failed_login_attempts = 0
        user.locked_until = None
        session.flush()

    # A user can now hold several memberships in the same tenant (one per customer, or a
    # tenant-wide one plus per-customer overrides — domain/tenant_membership.py), so this
    # is no longer a single-row lookup.
    active_memberships = [
        m
        for m in session.execute(
            select(TenantMembership).where(TenantMembership.user_id == user.id, TenantMembership.tenant_id == tenant.id)
        )
        .scalars()
        .all()
        if m.is_active
    ]
    if not active_memberships:
        return PasswordLoginResult(PasswordLoginOutcome.INVALID_CREDENTIALS)

    # Password correctness is already confirmed above — only NOW do we distinguish
    # "no access at all" (already handled) from "access exists, but this scope requires
    # SSO," so a wrong guess never reveals which scopes require SSO for this identity.
    password_allowed_memberships = [m for m in active_memberships if _scope_allows_password_login(session, tenant, m)]
    if not password_allowed_memberships:
        return PasswordLoginResult(PasswordLoginOutcome.SSO_REQUIRED)

    # Login picks a default rather than asking the user to disambiguate here: the
    # tenant-wide membership if they have one (the only kind that existed before
    # customers did, so this keeps today's single-membership case byte-for-byte
    # unchanged), else the earliest-created customer membership. Reaching any other one
    # is what "Switch organization" is for post-login — a full login-time disambiguation
    # step across several customer scopes is deliberately out of scope.
    membership = next(
        (m for m in password_allowed_memberships if m.customer_id is None),
        # created_at alone isn't a safe sort key: SQLite's CURRENT_TIMESTAMP only has
        # one-second resolution, so two memberships created in the same second (e.g. from
        # a single bulk CSV upload) would tie, and without a secondary key the "earliest"
        # pick would depend on incidental row-fetch order rather than being
        # deterministic. id is an arbitrary but stable tiebreaker — not truly "earliest"
        # on a tie, but at least reproducible for a given dataset.
        sorted(password_allowed_memberships, key=lambda m: (m.created_at, m.id))[0],
    )

    has_key = user_has_webauthn_credentials(session, tenant.id, user.id)
    mfa_required = has_key or membership.require_webauthn
    needs_webauthn_enrollment = membership.require_webauthn and not has_key
    identity = AuthenticatedIdentity(
        user=user,
        tenant=tenant,
        membership=membership,
        mfa_required=mfa_required,
        needs_webauthn_enrollment=needs_webauthn_enrollment,
    )
    return PasswordLoginResult(PasswordLoginOutcome.SUCCESS, identity=identity)


def _scope_allows_password_login(session: Session, tenant: Tenant, membership: TenantMembership) -> bool:
    # A membership explicitly flagged to require WebAuthn may use a password even where
    # the tenant/customer otherwise mandates SSO — the mandatory key (not group-mapped
    # SSO) is the compensating control for this specific membership. See
    # domain/tenant_membership.py::require_webauthn.
    if membership.require_webauthn:
        return True
    if membership.customer_id is None:
        return tenant.password_login_enabled
    customer = session.get(Customer, membership.customer_id)
    return customer is not None and customer.password_login_enabled
