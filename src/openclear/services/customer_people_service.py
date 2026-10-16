# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""A customer's own administrators managing its people (CUSTOMER_ADMIN_PLAN.md, CA2).

Everything here acts within one customer of one bank, and only while the bank lets that
customer manage itself (Customer.self_manages_users, checked again here as well as by the
permission mask in auth/deps.py). The guardrails:

- only the groups the bank offers to customers (SecurityGroup.offered_to_customers);
- nobody is linked silently: people are invited by email, and someone who already exists
  accepts with their own password;
- actions that reach a person's whole identity (a password reset, an unlock, signing them
  out everywhere) only for someone who belongs to this customer alone
  (db/tenancy.py::memberships_elsewhere);
- the customer's last administrator can't be removed or demoted here; the bank can."""

import hashlib
import re
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from openclear.auth.password_policy import effective_policy, validate_password
from openclear.auth.security import hash_password, verify_password
from openclear.db.tenancy import bind_session_to_tenant, memberships_elsewhere
from openclear.domain.customer import Customer
from openclear.domain.customer_admin import UserInvitation
from openclear.domain.security_group import SecurityGroup
from openclear.domain.tenant import Tenant
from openclear.domain.tenant_membership import TenantMembership
from openclear.domain.user import User
from openclear.services import account_access_service, session_service

ADMIN_PERMISSION = "customer_user:manage"
INVITATION_DAYS = 7
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class PeopleError(ValueError):
    """Refused; the message is fit to show."""


@dataclass(frozen=True, slots=True)
class Person:
    membership: TenantMembership
    user: User
    group: SecurityGroup | None
    accounts: str  # "All accounts" or the chosen account numbers
    is_admin: bool
    belongs_elsewhere: bool  # identity-wide actions aren't offered


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _customer(session: Session, tenant_id: uuid.UUID, customer_id: uuid.UUID) -> Customer:
    customer = session.get(Customer, customer_id)
    if customer is None or customer.tenant_id != tenant_id or not customer.is_active:
        raise PeopleError("Customer not found.")
    if not customer.self_manages_users:
        raise PeopleError("Your bank manages this organization's people.")
    return customer


def offered_groups(session: Session, tenant_id: uuid.UUID) -> list[SecurityGroup]:
    return list(session.execute(select(SecurityGroup).where(
        SecurityGroup.tenant_id == tenant_id, SecurityGroup.offered_to_customers.is_(True)).order_by(SecurityGroup.name)).scalars())


def _offered_group(session: Session, tenant_id: uuid.UUID, group_id: uuid.UUID) -> SecurityGroup:
    group = session.get(SecurityGroup, group_id)
    if group is None or group.tenant_id != tenant_id or not group.offered_to_customers:
        raise PeopleError("Choose one of the roles your bank offers.")
    return group


def _is_admin(session: Session, membership: TenantMembership) -> bool:
    group = session.get(SecurityGroup, membership.security_group_id)
    return bool(membership.is_active and group is not None and ADMIN_PERMISSION in group.permissions)


def administrators(session: Session, tenant_id: uuid.UUID, customer_id: uuid.UUID) -> list[TenantMembership]:
    """The customer's active people who can manage its people."""
    return [m for m in _memberships(session, tenant_id, customer_id) if _is_admin(session, m)]


def _memberships(session: Session, tenant_id: uuid.UUID, customer_id: uuid.UUID) -> list[TenantMembership]:
    return list(session.execute(
        select(TenantMembership).join(User, User.id == TenantMembership.user_id).where(
            TenantMembership.tenant_id == tenant_id, TenantMembership.customer_id == customer_id,
            User.is_service_account.is_(False))).scalars())


def _member(session: Session, tenant_id: uuid.UUID, customer_id: uuid.UUID, membership_id: uuid.UUID) -> tuple[TenantMembership, User]:
    membership = session.get(TenantMembership, membership_id)
    user = session.get(User, membership.user_id) if membership is not None else None
    if (membership is None or membership.tenant_id != tenant_id or membership.customer_id != customer_id
            or user is None or user.is_service_account):
        raise PeopleError("Person not found.")
    return membership, user


def _keep_an_admin(session: Session, tenant_id: uuid.UUID, customer_id: uuid.UUID, membership: TenantMembership) -> None:
    others = [m for m in administrators(session, tenant_id, customer_id) if m.id != membership.id]
    if _is_admin(session, membership) and not others:
        raise PeopleError("This is your organization's last administrator. Make someone else an administrator first, "
                          "or ask your bank.")


def list_people(session: Session, tenant_id: uuid.UUID, customer_id: uuid.UUID) -> list[Person]:
    _customer(session, tenant_id, customer_id)
    people = []
    for membership in _memberships(session, tenant_id, customer_id):
        user = session.get(User, membership.user_id)
        group = session.get(SecurityGroup, membership.security_group_id)
        access = account_access_service.get_access(session, tenant_id, membership.id)
        people.append(Person(
            membership=membership, user=user, group=group,
            accounts="All accounts" if access.all_accounts else account_access_service.describe(access).split(": ", 1)[-1],
            is_admin=_is_admin(session, membership),
            belongs_elsewhere=bool(memberships_elsewhere(session, user.id, tenant_id, customer_id=customer_id, within_customer=True)),
        ))
    return sorted(people, key=lambda p: (not p.membership.is_active, p.user.email.lower()))


def get_person(session: Session, tenant_id: uuid.UUID, customer_id: uuid.UUID, membership_id: uuid.UUID) -> Person:
    _member(session, tenant_id, customer_id, membership_id)
    return next(p for p in list_people(session, tenant_id, customer_id) if p.membership.id == membership_id)


def pending_invitations(session: Session, tenant_id: uuid.UUID, customer_id: uuid.UUID) -> list[UserInvitation]:
    now = _now()
    rows = session.execute(select(UserInvitation).where(
        UserInvitation.tenant_id == tenant_id, UserInvitation.customer_id == customer_id,
        UserInvitation.accepted_at.is_(None), UserInvitation.revoked_at.is_(None)).order_by(UserInvitation.created_at.desc())).scalars()
    return [i for i in rows if _aware(i.expires_at) > now]


def _validate_accounts(session: Session, tenant_id: uuid.UUID, customer_id: uuid.UUID, all_accounts: bool,
                       account_ids: list[uuid.UUID]) -> list[uuid.UUID]:
    if all_accounts:
        return []
    from openclear.domain.account import Account

    wanted = set(account_ids)
    if not wanted:
        raise PeopleError("Choose at least one account, or all of them.")
    found = set(session.execute(select(Account.id).where(
        Account.tenant_id == tenant_id, Account.customer_id == customer_id, Account.id.in_(wanted))).scalars())
    if found != wanted:
        raise PeopleError("Choose only your organization's accounts.")
    return sorted(wanted, key=str)


def invite(
    session: Session, tenant_id: uuid.UUID, customer_id: uuid.UUID, *, email: str, security_group_id: uuid.UUID,
    all_accounts: bool = True, account_ids: list[uuid.UUID] | None = None, require_webauthn: bool = False,
    invited_by_user_id: uuid.UUID | None, accept_base_url: str,
) -> UserInvitation:
    """Invites one person; the email carries a one-time link. A newer invitation for the same
    email replaces an unused one."""
    customer = _customer(session, tenant_id, customer_id)
    email = (email or "").strip()
    if not _EMAIL.match(email):
        raise PeopleError("Enter an email address.")
    _offered_group(session, tenant_id, security_group_id)
    chosen = _validate_accounts(session, tenant_id, customer_id, all_accounts, account_ids or [])
    existing = session.execute(select(User).where(User.email == email)).scalar_one_or_none()
    if existing is not None and existing.is_service_account:
        raise PeopleError("That email belongs to an integration, not a person.")
    if existing is not None:
        here = session.execute(select(TenantMembership).where(
            TenantMembership.user_id == existing.id, TenantMembership.tenant_id == tenant_id,
            TenantMembership.customer_id == customer_id)).scalar_one_or_none()
        if here is not None:
            raise PeopleError("They're already in your organization." if here.is_active
                              else "They're already in your organization, but deactivated: reactivate them instead.")
    for old in pending_invitations(session, tenant_id, customer_id):
        if old.email.lower() == email.lower():
            old.revoked_at = _now()
    token = f"{tenant_id.hex}.{secrets.token_urlsafe(32)}"
    invitation = UserInvitation(
        tenant_id=tenant_id, customer_id=customer_id, email=email, security_group_id=security_group_id,
        all_accounts=all_accounts, account_ids=[str(a) for a in chosen], require_webauthn=require_webauthn,
        token_hash=_hash(token), invited_by_user_id=invited_by_user_id, expires_at=_now() + timedelta(days=INVITATION_DAYS),
    )
    session.add(invitation)
    session.flush()
    from openclear.services import notification_service

    tenant = session.get(Tenant, tenant_id)
    inviter = session.get(User, invited_by_user_id) if invited_by_user_id else None
    notification_service.notify_invitation(
        session, tenant_id=tenant_id, email=email, link=f"{accept_base_url.rstrip('/')}/ui/invitations/{token}",
        organization=customer.name, bank_name=tenant.name, invited_by=inviter.email if inviter else None,
        expires_days=INVITATION_DAYS, invitation_id=invitation.id,
    )
    return invitation


def revoke_invitation(session: Session, tenant_id: uuid.UUID, customer_id: uuid.UUID, invitation_id: uuid.UUID) -> UserInvitation:
    _customer(session, tenant_id, customer_id)
    invitation = session.get(UserInvitation, invitation_id)
    if invitation is None or invitation.tenant_id != tenant_id or invitation.customer_id != customer_id or invitation.accepted_at:
        raise PeopleError("Invitation not found.")
    invitation.revoked_at = invitation.revoked_at or _now()
    session.flush()
    return invitation


# --- Accepting -------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class OpenInvitation:
    invitation: UserInvitation
    customer: Customer
    tenant: Tenant
    existing_user: User | None  # they confirm with their own password
    password_sign_in: bool  # False: the customer signs in only through its identity provider


def open_invitation(session: Session, token: str) -> OpenInvitation | None:
    """The invitation behind a link, if it can still be used. Binds the session to its bank."""
    tenant_hex, _, _secret = (token or "").partition(".")
    try:
        tenant_id = uuid.UUID(hex=tenant_hex)
    except ValueError:
        return None
    tenant = session.get(Tenant, tenant_id)
    if tenant is None or not tenant.is_active:
        return None
    bind_session_to_tenant(session, tenant_id)
    invitation = session.execute(select(UserInvitation).where(UserInvitation.token_hash == _hash(token))).scalar_one_or_none()
    if (invitation is None or invitation.tenant_id != tenant_id or invitation.accepted_at or invitation.revoked_at
            or _aware(invitation.expires_at) <= _now()):
        return None
    customer = session.get(Customer, invitation.customer_id)
    if customer is None or not customer.is_active or not customer.self_manages_users:
        return None
    user = session.execute(select(User).where(User.email == invitation.email)).scalar_one_or_none()
    return OpenInvitation(invitation, customer, tenant, user,
                          password_sign_in=bool(tenant.password_login_enabled and customer.password_login_enabled))


def _join(session: Session, opened: OpenInvitation, user: User) -> TenantMembership:
    invitation = opened.invitation
    group = session.get(SecurityGroup, invitation.security_group_id)
    if group is None or not group.offered_to_customers:
        raise PeopleError("This invitation's role is no longer offered. Ask for a new invitation.")
    membership = TenantMembership(user_id=user.id, tenant_id=invitation.tenant_id, customer_id=invitation.customer_id,
                                  security_group_id=invitation.security_group_id, require_webauthn=invitation.require_webauthn)
    session.add(membership)
    session.flush()
    if not invitation.all_accounts:
        account_access_service.set_access(session, invitation.tenant_id, membership.id, all_accounts=False,
                                          account_ids=[uuid.UUID(a) for a in invitation.account_ids])
    invitation.accepted_at, invitation.accepted_user_id = _now(), user.id
    session.flush()
    from openclear.services import customer_approval_service

    customer_approval_service.hold_if_required(session, membership)  # MOBILE_PLAN.md R6
    return membership


def accept_as_new(session: Session, opened: OpenInvitation, password: str | None) -> tuple[User, TenantMembership]:
    """A person new to OpenClear: they choose a password (or, where the customer signs in only
    through its identity provider, none: they'll sign in there)."""
    if opened.existing_user is not None:
        raise PeopleError("You already have an OpenClear sign-in: confirm with its password.")
    if opened.password_sign_in:
        validate_password(password or "", effective_policy(opened.tenant, opened.customer))
        hashed = hash_password(password)
    else:
        hashed = hash_password(secrets.token_urlsafe(48))  # never told to anyone: they sign in through sign-on
    user = User(email=opened.invitation.email, hashed_password=hashed, is_active=True)
    session.add(user)
    session.flush()
    return user, _join(session, opened, user)


def accept_as_existing(session: Session, opened: OpenInvitation, current_password: str) -> tuple[User, TenantMembership]:
    """Someone who already signs in to OpenClear elsewhere proves it's them with their own
    password; only then are they added. A wrong password counts toward their lockout."""
    from openclear.config import get_settings
    from openclear.services import notification_service

    user = opened.existing_user
    if user is None or not user.is_active or user.is_service_account:
        raise PeopleError("This invitation can't be accepted. Ask for a new one.")
    now = _now()
    if user.locked_until is not None and _aware(user.locked_until) > now:
        raise PeopleError("Your sign-in is locked for now after too many attempts. Try again later.")
    if not verify_password(current_password or "", user.hashed_password):
        # Counted toward the same lockout as a failed sign-in (auth/login_service.py).
        settings = get_settings()
        user.failed_login_attempts += 1
        if user.failed_login_attempts >= settings.login_max_failed_attempts:
            user.locked_until = now + timedelta(minutes=settings.login_lockout_minutes)
            notification_service.notify_account_locked(session, user)
        session.flush()
        raise PeopleError("That isn't the password for this email.")
    user.failed_login_attempts, user.locked_until = 0, None
    return user, _join(session, opened, user)


# --- Changing people -------------------------------------------------------------------------


def update_person(
    session: Session, tenant_id: uuid.UUID, customer_id: uuid.UUID, membership_id: uuid.UUID, *, security_group_id: uuid.UUID,
    require_webauthn: bool, all_accounts: bool, account_ids: list[uuid.UUID] | None = None,
) -> TenantMembership:
    _customer(session, tenant_id, customer_id)
    membership, _user = _member(session, tenant_id, customer_id, membership_id)
    if security_group_id == membership.security_group_id:
        group = session.get(SecurityGroup, membership.security_group_id)  # unchanged: may be a role the bank gave them
    else:
        group = _offered_group(session, tenant_id, security_group_id)
    if ADMIN_PERMISSION not in group.permissions:
        _keep_an_admin(session, tenant_id, customer_id, membership)
    chosen = _validate_accounts(session, tenant_id, customer_id, all_accounts, account_ids or [])
    membership.security_group_id = group.id
    membership.require_webauthn = require_webauthn
    account_access_service.set_access(session, tenant_id, membership.id, all_accounts=all_accounts, account_ids=chosen)
    session.flush()
    return membership


def set_active(session: Session, tenant_id: uuid.UUID, customer_id: uuid.UUID, membership_id: uuid.UUID, active: bool) -> TenantMembership:
    _customer(session, tenant_id, customer_id)
    membership, _user = _member(session, tenant_id, customer_id, membership_id)
    if active and membership.pending_approval_at is not None:
        raise PeopleError("They're waiting for your bank to approve them; only your bank can let them in.")
    if not active:
        _keep_an_admin(session, tenant_id, customer_id, membership)
    membership.is_active = active
    session.flush()
    return membership


def _whole_identity(session: Session, tenant_id: uuid.UUID, customer_id: uuid.UUID, membership_id: uuid.UUID) -> User:
    """The person, for an action on their whole sign-in: only if they belong to this customer alone."""
    _customer(session, tenant_id, customer_id)
    _membership, user = _member(session, tenant_id, customer_id, membership_id)
    if memberships_elsewhere(session, user.id, tenant_id, customer_id=customer_id, within_customer=True):
        raise PeopleError(f"{user.email} also belongs to another organization, so only they (or your bank) can do this.")
    return user


def unlock(session: Session, tenant_id: uuid.UUID, customer_id: uuid.UUID, membership_id: uuid.UUID) -> User:
    from openclear.services import notification_service

    user = _whole_identity(session, tenant_id, customer_id, membership_id)
    user.failed_login_attempts, user.locked_until = 0, None
    session.flush()
    notification_service.notify_account_unlocked(session, user)
    return user


def reset_password(session: Session, tenant_id: uuid.UUID, customer_id: uuid.UUID, membership_id: uuid.UUID,
                   *, actor_user_id: uuid.UUID) -> tuple[User, str]:
    """A temporary password, shown once to the administrator; they must change it at sign-in."""
    from openclear.services import user_service

    _whole_identity(session, tenant_id, customer_id, membership_id)
    try:
        return user_service.admin_reset_password(session, tenant_id, membership_id, actor_user_id=actor_user_id)
    except user_service.PasswordChangeError as exc:
        raise PeopleError(str(exc)) from None


def sign_out_everywhere(session: Session, tenant_id: uuid.UUID, customer_id: uuid.UUID, membership_id: uuid.UUID) -> User:
    user = _whole_identity(session, tenant_id, customer_id, membership_id)
    session_service.revoke_all_sessions(session, user)
    return user


# --- The bank's switches -----------------------------------------------------------------------


def set_self_service(session: Session, tenant_id: uuid.UUID, customer_id: uuid.UUID, *, users: bool, sso: bool,
                     api_clients: bool | None = None) -> Customer:
    """The bank lets a customer manage its own people, sign-on and API clients, or stops it (at
    once). `api_clients` None leaves that switch as it is."""
    customer = session.get(Customer, customer_id)
    if customer is None or customer.tenant_id != tenant_id:
        raise PeopleError("Customer not found.")
    customer.self_manages_users, customer.self_manages_sso = users, sso
    if api_clients is not None:
        customer.self_manages_api_clients = api_clients
    session.flush()
    return customer
