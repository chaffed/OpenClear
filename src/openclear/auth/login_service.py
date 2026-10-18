# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import enum
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from openclear.db.tenancy import bind_session_to_tenant
from openclear.auth.security import hash_password, password_needs_rehash, verify_password
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
    # The password was CORRECT, but the person's only membership here waits for the bank's
    # approval (MOBILE_PLAN.md R6; services/customer_approval_service.py).
    PENDING_APPROVAL = "pending_approval"


@dataclass(frozen=True, slots=True)
class PasswordLoginResult:
    outcome: PasswordLoginOutcome
    identity: AuthenticatedIdentity | None = None


# "Locked until an administrator unlocks it" (Tenant.lockout_until_unlocked): far enough ahead never to pass.
LOCKED_UNTIL_UNLOCKED = datetime(9999, 12, 31, tzinfo=timezone.utc)


_DUMMY_HASH: str | None = None


def _spend_a_password_check(password: str) -> None:
    """Runs bcrypt against a throwaway hash, so a sign-in that fails before the real check
    (unknown bank, unknown or inactive person, a locked account) takes as long as a wrong
    password and doesn't reveal which emails have accounts (ASVS 5.0 6.3.8)."""
    global _DUMMY_HASH
    if _DUMMY_HASH is None:
        import secrets

        from openclear.auth.security import hash_password

        _DUMMY_HASH = hash_password(secrets.token_urlsafe(24))
    verify_password(password, _DUMMY_HASH)


def lockout_policy(tenant: Tenant | None, settings=None) -> tuple[int, int, bool]:
    """(failed attempts that lock, minutes the lock lasts, whether it lasts until an
    administrator unlocks it): the bank's own, or the installation's."""
    settings = settings or get_settings()
    if tenant is None:
        return settings.login_max_failed_attempts, settings.login_lockout_minutes, False
    return (tenant.login_max_failed_attempts or settings.login_max_failed_attempts,
            tenant.login_lockout_minutes or settings.login_lockout_minutes, bool(tenant.lockout_until_unlocked))


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
    see domain/user.py), by the policy of the bank the attempt is against (lockout_policy). This function only flushes, never commits — callers
    (web/routers/auth.py::login_submit, api/v1/auth.py::login) must each commit right
    after calling this, or a failed attempt's counter increment is silently lost when the
    request's session closes."""
    settings = settings or get_settings()
    from openclear.services import security_events

    def failed(reason: str, tenant_id=None, user=None, outcome=PasswordLoginOutcome.INVALID_CREDENTIALS, name="auth.login.failed"):
        # Recorded for the bank's security team (SIEM_PLAN.md S2); the caller still learns
        # only "invalid credentials", never which part was wrong.
        security_events.emit(
            session, name, tenant_id=tenant_id, outcome="failure", reason=reason,
            actor_type="user", actor_id=user.id if user else None, actor_label=email[:320],
        )
        return PasswordLoginResult(outcome)

    tenant = session.execute(select(Tenant).where(Tenant.slug == tenant_slug)).scalar_one_or_none()
    if tenant is None or not tenant.is_active:
        _spend_a_password_check(password)
        return failed("unknown_bank")
    # The rest of login reads RLS-protected tables (memberships, security groups, keys),
    # so the session runs as this bank from here on (a no-op off Postgres).
    bind_session_to_tenant(session, tenant.id)

    user = session.execute(select(User).where(User.email == email)).scalar_one_or_none()
    if user is None or not user.is_active or user.is_service_account:
        _spend_a_password_check(password)
        return failed("unknown_user" if user is None else "inactive", tenant.id, user)

    if user.locked_until is not None and _as_utc(user.locked_until) > datetime.now(timezone.utc):
        _spend_a_password_check(password)
        return failed("locked", tenant.id, user, PasswordLoginOutcome.LOCKED)

    if not verify_password(password, user.hashed_password):
        user.failed_login_attempts += 1
        attempts, minutes, until_unlocked = lockout_policy(tenant, settings)
        if user.failed_login_attempts >= attempts:
            user.locked_until = LOCKED_UNTIL_UNLOCKED if until_unlocked else datetime.now(timezone.utc) + timedelta(minutes=minutes)
            session.flush()
            # Only fires the moment the lockout threshold is actually crossed, not on
            # every subsequent rejected attempt while already locked (that's the
            # early-return at the top of this function, above).
            notification_service.notify_account_locked(session, user, minutes=None if until_unlocked else minutes)
            failed("invalid_credentials", tenant.id, user)
            return failed("threshold_reached", tenant.id, user, PasswordLoginOutcome.LOCKED, name="auth.lockout")
        session.flush()
        return failed("invalid_credentials", tenant.id, user)

    if password_needs_rehash(user.hashed_password):
        user.hashed_password = hash_password(password)  # upgrade a pre-1.22.1 hash while we hold the password
    # Correct password proves the brute force didn't work, regardless of whether the
    # login ultimately succeeds or hits SSO_REQUIRED below.
    if user.failed_login_attempts or user.locked_until is not None:
        user.failed_login_attempts = 0
        user.locked_until = None
        session.flush()

    # A user can now hold several memberships in the same tenant (one per customer, or a
    # tenant-wide one plus per-customer overrides — domain/tenant_membership.py), so this
    # is no longer a single-row lookup.
    memberships = session.execute(
        select(TenantMembership).where(TenantMembership.user_id == user.id, TenantMembership.tenant_id == tenant.id)
    ).scalars().all()
    active_memberships = [m for m in memberships if m.is_active]
    if not active_memberships:
        if any(m.pending_approval_at is not None for m in memberships):
            return failed("pending_approval", tenant.id, user, PasswordLoginOutcome.PENDING_APPROVAL)
        return failed("no_membership", tenant.id, user)

    # Password correctness is already confirmed above — only NOW do we distinguish
    # "no access at all" (already handled) from "access exists, but this scope requires
    # SSO," so a wrong guess never reveals which scopes require SSO for this identity.
    password_allowed_memberships = [m for m in active_memberships if scope_allows_password_login(session, tenant, m)]
    if not password_allowed_memberships:
        return failed("sso_required", tenant.id, user, PasswordLoginOutcome.SSO_REQUIRED)

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
    from openclear.auth.security_key_policy import requirement

    enforced = requirement(session, tenant, membership).enforced  # the membership's, bank's or customer's (OW-D18)
    mfa_required = has_key or enforced
    needs_webauthn_enrollment = enforced and not has_key
    identity = AuthenticatedIdentity(
        user=user,
        tenant=tenant,
        membership=membership,
        mfa_required=mfa_required,
        needs_webauthn_enrollment=needs_webauthn_enrollment,
    )
    _flag_weak_password(session, tenant, membership, user, password)
    security_events.emit(
        session, "auth.login.succeeded", tenant_id=tenant.id, actor_type="user", actor_id=user.id, actor_label=user.email,
        details={"method": "password", "second_factor_required": mfa_required},
    )
    return PasswordLoginResult(PasswordLoginOutcome.SUCCESS, identity=identity)


def _flag_weak_password(session: Session, tenant: Tenant, membership: TenantMembership, user, password: str) -> None:
    """Sign-in is the only moment OpenClear holds the plaintext of an existing password, so
    it's checked here against today's rules (OWASP_PLAN.md OW-D17): a password that is known
    to be breached, or shorter than the policy now requires, still lets the person in, but
    they must change it before going further (must_change_password). Once per person; not
    in the demo organization, whose shared password can't be changed."""
    if user.must_change_password or tenant.is_demo:
        return
    from openclear.auth.breached_passwords import is_breached
    from openclear.auth.password_policy import effective_policy
    from openclear.services import security_events

    customer = session.get(Customer, membership.customer_id) if membership.customer_id else None
    if len(password) < effective_policy(tenant, customer).min_length:
        reason = "too_short"
    elif is_breached(password):
        reason = "breached"
    else:
        return
    user.must_change_password = True
    session.flush()
    security_events.emit(
        session, "auth.password.weak", tenant_id=tenant.id, actor_type="user", actor_id=user.id, actor_label=user.email,
        outcome="success", reason=reason,
    )


def scope_allows_password_login(session: Session, tenant: Tenant, membership: TenantMembership) -> bool:
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


def authenticate_platform(session: Session, email: str, password: str, *, settings: Settings | None = None) -> User | None:
    """Platform staff signing in to the platform pages (web/platform_session.py; UX_PLAN.md
    U8, U9): the same lockout as a bank sign-in, on the same identity. Returns the user, or
    None (the caller says only "invalid credentials"). Flushes; the caller commits."""
    settings = settings or get_settings()
    from openclear.services import security_events

    def failed(reason: str, user=None, name="auth.login.failed") -> None:
        security_events.emit(session, name, tenant_id=None, outcome="failure", reason=reason, actor_type="user",
                             actor_id=user.id if user else None, actor_label=email[:320], details={"where": "platform"})

    user = session.execute(select(User).where(User.email == (email or "").strip().lower())).scalar_one_or_none()
    if user is None or not user.is_active or user.is_service_account or user.platform_role is None:
        failed("unknown_user" if user is None else "not_platform_staff", user)
        return None
    if user.locked_until is not None and _as_utc(user.locked_until) > datetime.now(timezone.utc):
        failed("locked", user)
        return None
    if not verify_password(password, user.hashed_password):
        user.failed_login_attempts += 1
        if user.failed_login_attempts >= settings.login_max_failed_attempts:
            user.locked_until = datetime.now(timezone.utc) + timedelta(minutes=settings.login_lockout_minutes)
            notification_service.notify_account_locked(session, user)
            failed("threshold_reached", user, name="auth.lockout")
        else:
            failed("invalid_credentials", user)
        session.flush()
        return None
    user.failed_login_attempts, user.locked_until = 0, None
    if password_needs_rehash(user.hashed_password):
        user.hashed_password = hash_password(password)
    security_events.emit(session, "auth.login.succeeded", tenant_id=None, actor_type="user", actor_id=user.id,
                         actor_label=user.email, details={"method": "password", "where": "platform", "role": user.platform_role})
    session.flush()
    return user
