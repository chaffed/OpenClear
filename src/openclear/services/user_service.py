# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from openclear.errors import UserFacingError
from openclear.auth.password_policy import PasswordPolicy, context_words, effective_policy, strictest, validate_password
from openclear.auth.security import hash_password, verify_password
from openclear.bulk_import.fields import RowFieldError, optional_str, require_str
from openclear.bulk_import.results import UserBulkRowResult
from openclear.db.tenancy import acting_as_tenant, memberships_elsewhere
from openclear.domain.tenant import Tenant
from openclear.domain.tenant_membership import TenantMembership
from openclear.domain.user import User
from openclear.repositories.tenant_membership_repo import TenantMembershipRepository
from openclear.repositories.user_repo import UserRepository
from openclear.services import customer_service, notification_service, security_group_service, session_service


@dataclass(frozen=True, slots=True)
class TenantUserRow:
    membership: TenantMembership
    user: User
    security_group_name: str
    customer_name: str | None = None


@dataclass(frozen=True, slots=True)
class MembershipRow:
    membership: TenantMembership
    tenant: Tenant
    customer_name: str | None = None


@dataclass(frozen=True, slots=True)
class AddUserResult:
    outcome: Literal["created", "already_member", "needs_confirmation", "failed"]
    error: str | None = None
    # Set only when outcome == "created" -- lets a bulk caller
    # (services/bulk_upload_reversal_service.py) track the specific membership row this
    # call produced, for backing it out later.
    membership_id: uuid.UUID | None = None


def record_login(session: Session, user_id: uuid.UUID, *, tenant_id: uuid.UUID | None = None) -> None:
    """Called once per completed login (not token refresh, not "switch organization") —
    see the call sites in web/routers/auth.py, web/routers/embed.py, api/v1/auth.py and
    api/v1/webauthn.py. Powers the last_login_at column an access-review/recertification
    process (GET /api/v1/users) relies on to flag stale accounts, and, for the bank signed in
    to, each of the person's memberships' last_active_at, from which inactive access is
    deactivated and removed (services/inactivity_service.py)."""
    user = UserRepository(session).get(user_id)
    if user is None:
        return
    now = datetime.now(timezone.utc)
    user.last_login_at = now
    if tenant_id is not None:
        for membership in session.execute(select(TenantMembership).where(TenantMembership.user_id == user_id,
                                                                         TenantMembership.tenant_id == tenant_id)).scalars():
            membership.last_active_at = now
    session.flush()


def list_tenant_users(
    session: Session,
    tenant_id: uuid.UUID,
    *,
    limit: int | None = None,
    offset: int | None = None,
    order_by: Any = None,
) -> list[TenantUserRow]:
    # API clients' service identities aren't people: they're listed and managed on the API
    # clients page (services/api_client_service.py), not here.
    query = (
        TenantMembershipRepository(session, tenant_id).query()
        .join(User, User.id == TenantMembership.user_id)
        .where(User.is_service_account.is_(False))
    )
    if order_by is not None:
        query = query.order_by(*order_by) if isinstance(order_by, (tuple, list)) else query.order_by(order_by)
    if offset:
        query = query.offset(offset)
    if limit is not None:
        query = query.limit(limit)
    memberships = list(session.execute(query).scalars())
    user_repo = UserRepository(session)
    rows: list[TenantUserRow] = []
    for membership in memberships:
        user = user_repo.get(membership.user_id)
        group = security_group_service.get_security_group(session, tenant_id, membership.security_group_id)
        if user is None or group is None:
            continue
        customer_name = None
        if membership.customer_id is not None:
            customer = customer_service.get_customer(session, tenant_id, membership.customer_id)
            customer_name = customer.name if customer else None
        rows.append(
            TenantUserRow(membership=membership, user=user, security_group_name=group.name, customer_name=customer_name)
        )
    return rows


def count_tenant_users(session: Session, tenant_id: uuid.UUID) -> int:
    """How many rows list_tenant_users has in all (people only, not API clients)."""
    query = (
        TenantMembershipRepository(session, tenant_id).query()
        .join(User, User.id == TenantMembership.user_id)
        .where(User.is_service_account.is_(False))
    )
    return session.execute(select(func.count()).select_from(query.subquery())).scalar_one()


def list_memberships_for_user(session: Session, user_id: uuid.UUID) -> list[MembershipRow]:
    """The one deliberately cross-tenant query in the app: which tenants (and, within
    each, which customer scope) does this identity belong to. Powers "switch
    organization" (web/routers/tenant_switch.py) — never goes through
    TenantScopedRepository, which always filters to a single tenant; documented the same
    way exception_item/decision document their own RLS exclusion."""
    memberships = (
        session.execute(select(TenantMembership).where(TenantMembership.user_id == user_id, TenantMembership.is_active))
        .scalars()
        .all()
    )
    rows: list[MembershipRow] = []
    for membership in memberships:
        tenant = session.get(Tenant, membership.tenant_id)
        if tenant is None or not tenant.is_active:
            continue
        customer_name = None
        if membership.customer_id is not None:
            # Postgres RLS: the customer lives in that bank, which this user belongs to.
            with acting_as_tenant(session, membership.tenant_id):
                customer = customer_service.get_customer(session, membership.tenant_id, membership.customer_id)
            customer_name = customer.name if customer else None
        rows.append(MembershipRow(membership=membership, tenant=tenant, customer_name=customer_name))
    return rows


def create_user_with_membership(
    session: Session,
    tenant_id: uuid.UUID,
    *,
    email: str,
    password: str,
    security_group_id: uuid.UUID,
    customer_id: uuid.UUID | None = None,
    require_webauthn: bool = False,
) -> User:
    tenant = session.get(Tenant, tenant_id)
    customer = customer_service.get_customer(session, tenant_id, customer_id) if customer_id else None
    validate_password(password, effective_policy(tenant, customer),
                      context=context_words(email=email, tenants=[tenant], customers=[customer] if customer else []))

    user = User(email=email, hashed_password=hash_password(password))
    UserRepository(session).add(user)
    session.flush()
    membership = TenantMembership(
        user_id=user.id,
        tenant_id=tenant_id,
        customer_id=customer_id,
        security_group_id=security_group_id,
        require_webauthn=require_webauthn,
    )
    TenantMembershipRepository(session, tenant_id).add(membership)
    session.flush()
    return user


def confirm_cross_tenant_membership(
    session: Session,
    tenant_id: uuid.UUID,
    *,
    email: str,
    security_group_id: uuid.UUID,
    customer_id: uuid.UUID | None = None,
    require_webauthn: bool = False,
) -> TenantMembership | None:
    """Re-resolves the identity BY EMAIL again here, server-side, rather than trusting a
    client-supplied user id — this confirmation step exists precisely so an admin
    explicitly agrees to grant an *existing* identity access to this tenant, and that
    grant must never be inferable from anything a client request could tamper with.
    Returns None only if the identity no longer exists (a race, not the normal path);
    idempotent if a membership in this exact (tenant, customer) scope already exists."""
    user = UserRepository(session).get_by_email(email)
    if user is None or user.is_service_account:
        return None
    existing = [
        m for m in TenantMembershipRepository(session, tenant_id).list(user_id=user.id) if m.customer_id == customer_id
    ]
    if existing:
        return existing[0]
    membership = TenantMembership(
        user_id=user.id,
        tenant_id=tenant_id,
        customer_id=customer_id,
        security_group_id=security_group_id,
        require_webauthn=require_webauthn,
    )
    TenantMembershipRepository(session, tenant_id).add(membership)
    session.flush()
    return membership


def add_user(
    session: Session,
    tenant_id: uuid.UUID,
    *,
    email: str,
    password: str,
    security_group_id: uuid.UUID,
    customer_id: uuid.UUID | None = None,
    require_webauthn: bool = False,
) -> AddUserResult:
    """Single-user add flow (web/routers/users.py). A brand-new email is created
    immediately with the given password. An email that already belongs to an identity
    with a membership *in this same tenant* (any customer scope) is granted the new
    scoped membership directly — no confirmation needed, since the admin doing this
    already administers this tenant and the identity is already known within it. An
    email that only exists in a *different* tenant is never attached silently — the
    caller must re-post to confirm_cross_tenant_membership to actually grant it, after
    showing the admin an explicit confirmation step."""
    existing = UserRepository(session).get_by_email(email)
    if existing is not None and existing.is_service_account:
        return AddUserResult(outcome="failed", error="That email belongs to an API client, not a person.")
    if existing is None:
        if not password:
            return AddUserResult(outcome="failed", error="A password is required for a new user.")
        try:
            new_user = create_user_with_membership(
                session,
                tenant_id,
                email=email,
                password=password,
                security_group_id=security_group_id,
                customer_id=customer_id,
                require_webauthn=require_webauthn,
            )
        except ValueError as exc:
            return AddUserResult(outcome="failed", error=str(exc))
        new_membership = TenantMembershipRepository(session, tenant_id).list(user_id=new_user.id, customer_id=customer_id)[0]
        return AddUserResult(outcome="created", membership_id=new_membership.id)

    memberships_in_tenant = TenantMembershipRepository(session, tenant_id).list(user_id=existing.id)
    if any(m.customer_id == customer_id for m in memberships_in_tenant):
        return AddUserResult(outcome="already_member")

    if memberships_in_tenant:
        membership = TenantMembership(
            user_id=existing.id,
            tenant_id=tenant_id,
            customer_id=customer_id,
            security_group_id=security_group_id,
            require_webauthn=require_webauthn,
        )
        TenantMembershipRepository(session, tenant_id).add(membership)
        session.flush()
        return AddUserResult(outcome="created", membership_id=membership.id)

    return AddUserResult(outcome="needs_confirmation")


def get_access_for_email(session: Session, tenant_id: uuid.UUID, email: str) -> list[TenantUserRow]:
    """Every membership this email currently holds within THIS tenant only — never
    another tenant's. Deliberately no cross-tenant visibility: a bank must never be able
    to see what other, unrelated tenants a shared identity (e.g. an outside bookkeeper)
    also has access to, just by looking up their email. Trivial reuse of the exact same
    rows list_tenant_users already builds for /ui/users, filtered down to one email —
    the consolidated view for reviewing/extending one identity's footprint instead of
    paging through the full tenant user list."""
    return [row for row in list_tenant_users(session, tenant_id) if row.user.email.lower() == email.lower()]


def grant_multi_customer_access(
    session: Session,
    tenant_id: uuid.UUID,
    *,
    email: str,
    password: str,
    security_group_id: uuid.UUID,
    customer_ids: list[uuid.UUID | None],
    require_webauthn: bool = False,
) -> list[tuple[uuid.UUID | None, AddUserResult]]:
    """Grants one email access to several customer scopes (a None entry = bank-wide) in
    a single action — services/security_group_service.py's "Bookkeeper" preset is the
    motivating use case, but this works with any security group. Just add_user called
    once per requested scope, so every outcome it already handles (created /
    already_member / needs_confirmation / failed) comes through unchanged, with no new
    branching logic here. `password` only matters for this email's very first
    successful row in this call — by the second row, add_user already sees the identity
    as known within this tenant and never re-checks it. `require_webauthn` applies
    uniformly to every scope granted in this one call — see
    domain/tenant_membership.py::require_webauthn for what it does."""
    return [
        (
            customer_id,
            add_user(
                session,
                tenant_id,
                email=email,
                password=password,
                security_group_id=security_group_id,
                customer_id=customer_id,
                require_webauthn=require_webauthn,
            ),
        )
        for customer_id in customer_ids
    ]


def deactivate_membership(session: Session, tenant_id: uuid.UUID, membership_id: uuid.UUID) -> TenantMembership | None:
    membership = TenantMembershipRepository(session, tenant_id).get(membership_id)
    if membership is None:
        return None
    membership.is_active = False
    session.flush()
    return membership


def reactivate_membership(session: Session, tenant_id: uuid.UUID, membership_id: uuid.UUID) -> TenantMembership | None:
    membership = TenantMembershipRepository(session, tenant_id).get(membership_id)
    if membership is None:
        return None
    membership.is_active = True
    # A fresh start for the inactivity rules, or tonight's run would turn it straight off again.
    membership.last_active_at, membership.inactivity_warned_at = datetime.now(timezone.utc), None
    session.flush()
    return membership


def unlock_user(session: Session, tenant_id: uuid.UUID, membership_id: uuid.UUID) -> User | None:
    """Manual admin override for auth/login_service.py's auto-expiring lockout — resolved
    via a membership in the caller's own tenant (same tenant-scoping as
    deactivate_membership/reactivate_membership above), even though the lockout itself is
    tracked globally on User, so an admin can only unlock a user through a membership
    their own tenant actually owns."""
    membership = TenantMembershipRepository(session, tenant_id).get(membership_id)
    if membership is None:
        return None
    user = session.get(User, membership.user_id)
    if user is None:
        return None
    user.failed_login_attempts = 0
    user.locked_until = None
    session.flush()
    notification_service.notify_account_unlocked(session, user)
    return user


def admin_sign_out_everywhere(
    session: Session, tenant_id: uuid.UUID, membership_id: uuid.UUID, *, actor_user_id: uuid.UUID
) -> User | None:
    """Ends every session the user has (services/session_service.py::revoke_all_sessions).
    Resolved via a membership in the caller's own tenant, like unlock_user. Unlike a
    password reset, this is allowed for users who also belong to other organizations: it
    grants the admin nothing, and the user can simply sign in again. It does sign them out
    of those organizations too, since sessions belong to the person, not the organization.
    Returns None if the membership isn't this tenant's, or is the admin's own (use
    Security → Sign out other devices for that)."""
    membership = TenantMembershipRepository(session, tenant_id).get(membership_id)
    if membership is None or membership.user_id == actor_user_id:
        return None
    user = session.get(User, membership.user_id)
    if user is None:
        return None
    session_service.revoke_all_sessions(session, user)
    return user


class PasswordChangeError(UserFacingError):
    """A password change/reset was refused. The message is safe to show the user as-is
    (routers re-render the form with error=str(exc), this app's usual convention)."""


MIN_IDENTITY_CHECK = 10


def admin_reset_security_keys(
    session: Session, tenant_id: uuid.UUID, membership_id: uuid.UUID, *, actor_user_id: uuid.UUID, identity_check: str
) -> tuple[User, int]:
    """Removes every security key the person has at this bank, so they register a new one at
    their next sign-in (OWASP_PLAN.md OW-D21; ADR 0004). Keys belong to one bank, so their other
    organizations are unaffected. The administrator must say how they confirmed it's really the
    person (ASVS 5.0 6.4.4); it goes in the action log. Every session ends, and the person is
    emailed. Returns the user and how many keys were removed."""
    from sqlalchemy import delete

    from openclear.domain.webauthn_credential import WebauthnCredential

    identity_check = (identity_check or "").strip()
    if len(identity_check) < MIN_IDENTITY_CHECK:
        raise PasswordChangeError("Say how you confirmed it's really them, for example a call to their number on file.")
    membership = TenantMembershipRepository(session, tenant_id).get(membership_id)
    if membership is None:
        raise PasswordChangeError("User not found.")
    if membership.user_id == actor_user_id:
        raise PasswordChangeError("To change your own security keys, use Security.")
    user = session.get(User, membership.user_id)
    removed = session.execute(delete(WebauthnCredential).where(
        WebauthnCredential.tenant_id == tenant_id, WebauthnCredential.user_id == user.id)).rowcount or 0
    session_service.revoke_all_sessions(session, user)
    tenant = session.get(Tenant, tenant_id)
    notification_service.notify_security_key_changed(session, user, added=False, nickname=None,
                                                     bank_name=tenant.name if tenant else "your bank", reset_by_admin=True)
    session.flush()
    return user, removed


def password_policy_for_user(session: Session, user_id: uuid.UUID) -> PasswordPolicy | None:
    """The strictest policy across every active membership this identity holds — a User
    has ONE password shared by all of them, so it must satisfy the tightest one, not just
    the policy of whichever organization they happen to be signed into right now. Reuses
    list_memberships_for_user's deliberate cross-tenant lookup (same Postgres RLS caveat
    documented there: another tenant's customer-level additions may not be visible, in
    which case that tenant's own baseline still applies)."""
    policies = []
    for row in list_memberships_for_user(session, user_id):
        customer = (
            customer_service.get_customer(session, row.membership.tenant_id, row.membership.customer_id)
            if row.membership.customer_id
            else None
        )
        policies.append(effective_policy(row.tenant, customer))
    return strictest(policies)


def password_context_for_user(session: Session, user: User) -> list[str]:
    """Words this person's password mustn't contain: their email's, and every organization's
    and customer's they belong to (auth/password_policy.py::context_words)."""
    tenants, customers = [], []
    for row in list_memberships_for_user(session, user.id):
        tenants.append(row.tenant)
        if row.membership.customer_id:
            customer = customer_service.get_customer(session, row.membership.tenant_id, row.membership.customer_id)
            if customer is not None:
                customers.append(customer)
    return context_words(email=user.email, tenants=tenants, customers=customers)


def change_own_password(
    session: Session, tenant_id: uuid.UUID, user_id: uuid.UUID, *, current_password: str, new_password: str
) -> User:
    """Self-service change (web/routers/security_settings.py). Verifies the current
    password first — so a hijacked but still-open session can't silently take over the
    account permanently — and counts a wrong one toward the same lockout as a failed login
    (auth/login_service.py), so this form can't be used to brute-force it either. The
    caller commits (even on PasswordChangeError, so an incremented counter persists)."""
    tenant = session.get(Tenant, tenant_id)
    if tenant is not None and tenant.is_demo:
        # Shared public credentials: one visitor changing the password would lock every
        # other visitor out until the demo resets.
        raise PasswordChangeError("Passwords can't be changed in the demo organization.")

    user = session.get(User, user_id)
    if not verify_password(current_password, user.hashed_password):
        from openclear.auth import lockout

        lockout.record_failure(session, user, tenant)  # the bank's own lockout policy
        session.flush()
        raise PasswordChangeError("Your current password is incorrect.")
    if new_password == current_password:
        raise PasswordChangeError("Your new password must be different from your current one.")

    policy = password_policy_for_user(session, user_id)
    if policy is not None:
        try:
            validate_password(new_password, policy, context=password_context_for_user(session, user))
        except ValueError as exc:
            raise PasswordChangeError(str(exc)) from None

    user.hashed_password = hash_password(new_password)
    user.must_change_password = False
    user.failed_login_attempts = 0
    user.locked_until = None
    # Every other session (other devices, and anyone holding a stolen cookie) ends now.
    # The caller re-issues tokens for the session making this change, so the user
    # isn't signed out of the page they're on.
    session_service.revoke_all_sessions(session, user)
    notification_service.notify_password_changed(session, user, reset_by_admin=False)
    return user


def admin_reset_password(
    session: Session, tenant_id: uuid.UUID, membership_id: uuid.UUID, *, actor_user_id: uuid.UUID, base_url: str
) -> User:
    """Resets the user's password by emailing them a one-time link
    (services/password_reset_service.py); the administrator never sees or chooses the new
    password (ASVS 5.0 6.4.6). Resolved via a membership in the caller's own tenant, like unlock_user.

    Refused when the user also holds an active membership in any OTHER organization: a
    User's password is global, so otherwise one bank's admin could take over that
    person's access at another bank. They must change it themselves (decided
    2026-09-22, FIX_PLAN.md Phase 2). Also refused for the admin's own account (use the
    self-service page) and in the demo organization."""
    tenant = session.get(Tenant, tenant_id)
    if tenant is not None and tenant.is_demo:
        raise PasswordChangeError("Passwords can't be reset in the demo organization.")
    membership = TenantMembershipRepository(session, tenant_id).get(membership_id)
    if membership is None:
        raise PasswordChangeError("User not found.")
    user = session.get(User, membership.user_id)
    if user is None:
        raise PasswordChangeError("User not found.")
    if user.id == actor_user_id:
        raise PasswordChangeError("To change your own password, use Security → Change password.")
    # Counted with the RLS probe: under row-level security a plain query here would see only
    # this bank's memberships and let the reset through (db/tenancy.py::memberships_elsewhere).
    if memberships_elsewhere(session, user.id, tenant_id):
        raise PasswordChangeError(
            f"{user.email} also belongs to other organizations, so their password can't be reset from here. "
            "They must change it themselves from Security → Change password."
        )

    from openclear.services import password_reset_service

    try:
        password_reset_service.start(session, tenant, user, requested_by_user_id=actor_user_id, base_url=base_url)
    except password_reset_service.ResetError as exc:
        raise PasswordChangeError(str(exc)) from None
    return user


def update_membership(
    session: Session,
    tenant_id: uuid.UUID,
    membership_id: uuid.UUID,
    *,
    security_group_id: uuid.UUID,
    customer_id: uuid.UUID | None,
) -> TenantMembership:
    """Changes an existing membership's entitlements — which security group governs it,
    and/or which customer it's scoped to (or bank-wide, if customer_id is None). This is
    the only way to change either after creation; add_user/create_users_from_rows only
    ever set them once, at creation time. Raises ValueError (same convention as every
    other write-side validation in this app) for a membership/security group/customer
    that doesn't belong to this tenant, or a target customer scope that would collide
    with a DIFFERENT membership this same user already holds (the
    (user_id, tenant_id, customer_id) unique constraint from the customers feature) —
    reassigning to a scope this identity doesn't yet hold in this tenant is exactly what
    add_user already does when granting a same-tenant, different-scope membership; this
    just does it to an existing row instead of creating a new one."""
    membership = TenantMembershipRepository(session, tenant_id).get(membership_id)
    if membership is None:
        raise ValueError("Membership not found")

    group = security_group_service.get_security_group(session, tenant_id, security_group_id)
    if group is None:
        raise ValueError("Security group not found")

    if customer_id is not None:
        customer = customer_service.get_customer(session, tenant_id, customer_id)
        if customer is None:
            raise ValueError("Customer not found")

    conflicting = [
        m
        for m in TenantMembershipRepository(session, tenant_id).list(user_id=membership.user_id)
        if m.id != membership.id and m.customer_id == customer_id
    ]
    if conflicting:
        raise ValueError("This user already has a separate membership scoped to that customer")

    membership.security_group_id = security_group_id
    if membership.customer_id != customer_id:
        from openclear.services import account_access_service

        account_access_service.reset(session, membership)  # another customer's accounts: all of them, until limited again
    membership.customer_id = customer_id
    session.flush()
    return membership


def create_users_from_rows(
    session: Session, tenant_id: uuid.UUID, rows: list[dict[str, Any]], *, scoped_customer_id: uuid.UUID | None = None
) -> list[UserBulkRowResult]:
    """Entry point for the CSV bulk-load (web/routers/users.py). Expected
    (case/whitespace-insensitive) columns: email, security_group (a name resolved within
    this tenant), password (required only for rows whose email is brand new), and an
    optional customer_number. When the upload itself is customer-scoped
    (scoped_customer_id set — the uploader is scoped to one customer), every row is
    granted access to that same customer regardless of whether customer_number is
    present, and a present value must name that same customer; otherwise each row's own
    customer_number (if any) determines its scope, same pattern as bulk account uploads.
    Rows whose email already belongs to an identity in another tenant come back as
    'needs_confirmation' rather than being created — see add_user."""
    results: list[UserBulkRowResult] = []
    for index, row in enumerate(rows):
        row_label = f"row {index + 2}"  # +2: 1-based, plus the header row itself
        try:
            email = require_str(row, "email")
            group_name = require_str(row, "security_group")
            password = optional_str(row, "password") or ""
            group = security_group_service.get_security_group_by_name(session, tenant_id, group_name)
            if group is None:
                raise RowFieldError(f"No security group found named {group_name!r}")

            customer_number = optional_str(row, "customer_number")
            if scoped_customer_id is not None:
                if customer_number:
                    customer = customer_service.get_customer_by_number(session, tenant_id, customer_number)
                    if customer is None or customer.id != scoped_customer_id:
                        raise RowFieldError(f"customer_number {customer_number!r} does not match your assigned customer")
                row_customer_id = scoped_customer_id
            elif customer_number:
                customer = customer_service.get_customer_by_number(session, tenant_id, customer_number)
                if customer is None:
                    raise RowFieldError(f"No customer found with customer number {customer_number!r}")
                row_customer_id = customer.id
            else:
                row_customer_id = None

            result = add_user(
                session, tenant_id, email=email, password=password, security_group_id=group.id, customer_id=row_customer_id
            )
        except RowFieldError as exc:
            session.rollback()
            results.append(UserBulkRowResult(row_label=row_label, outcome="failed", error=str(exc)))
            continue
        except Exception as exc:  # noqa: BLE001 — isolate row failures in a bulk file
            session.rollback()
            results.append(UserBulkRowResult(row_label=row_label, outcome="failed", error=str(exc)))
            continue

        if result.outcome == "created":
            session.commit()
        else:
            session.rollback()
        results.append(
            UserBulkRowResult(
                row_label=row_label,
                outcome=result.outcome,
                email=email,
                security_group_id=group.id,
                error=result.error,
                membership_id=result.membership_id,
            )
        )
    return results


def confirm_users_bulk(
    session: Session, tenant_id: uuid.UUID, confirmations: list[tuple[str, uuid.UUID, uuid.UUID | None, bool]]
) -> list[UserBulkRowResult]:
    """Follow-up step for the 'needs_confirmation' rows a bulk CSV load (or the
    /ui/users/access/grant multi-customer tool) produced — the admin reviewed and
    re-submitted these (email, security_group_id, customer_id, require_webauthn)
    quadruples explicitly. The plain CSV bulk-user-upload flow never sets the last
    field (always False); only the access-grant tool's checkbox does."""
    results: list[UserBulkRowResult] = []
    for email, security_group_id, customer_id, require_webauthn in confirmations:
        membership = confirm_cross_tenant_membership(
            session,
            tenant_id,
            email=email,
            security_group_id=security_group_id,
            customer_id=customer_id,
            require_webauthn=require_webauthn,
        )
        if membership is None:
            session.rollback()
            results.append(UserBulkRowResult(row_label=email, outcome="failed", email=email, error="User no longer exists."))
        else:
            session.commit()
            results.append(UserBulkRowResult(row_label=email, outcome="created", email=email, security_group_id=security_group_id))
    return results
