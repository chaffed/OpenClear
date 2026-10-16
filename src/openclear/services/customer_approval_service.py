# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""The bank approves new customer users (MOBILE_PLAN.md R6; CUSTOMER_ADMIN_PLAN.md CA-D4). Off by
default. When on for a customer (Customer.approve_users, else the bank's
Tenant.approve_customer_users), a person its side adds waits for the bank:

- accepting an invitation from the customer's administrator;
- the customer's own sign-on creating them the first time they sign in;
- the customer's own SCIM directory creating them.

Their membership is made inactive with `pending_approval_at`; signing in says they're waiting
for their bank. Only the bank's approval (customer:manage) turns it on: their own sign-on, SCIM
and the customer's administrators can't. Rejecting removes the membership. Either way the
customer's administrators are told, and it's in the action log as a security event. People
the bank adds itself, and its own sign-on's people, never wait."""

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from openclear.domain.customer import Customer
from openclear.domain.security_group import SecurityGroup
from openclear.domain.tenant import Tenant
from openclear.domain.tenant_membership import MembershipAccount, TenantMembership
from openclear.domain.user import User


class ApprovalError(ValueError):
    """The message is fit to show."""


@dataclass
class Waiting:
    membership: TenantMembership
    email: str
    customer: Customer
    role: str | None
    since: datetime


def required(session: Session, tenant_id: uuid.UUID, customer_id: uuid.UUID | None) -> bool:
    """Whether a person this customer's side adds waits for the bank."""
    if customer_id is None:
        return False
    customer = session.get(Customer, customer_id)
    if customer is not None and customer.approve_users is not None:
        return customer.approve_users
    tenant = session.get(Tenant, tenant_id)
    return bool(tenant and tenant.approve_customer_users)


def hold(membership: TenantMembership) -> None:
    """A new membership waits: inactive until the bank approves it."""
    membership.is_active = False
    membership.pending_approval_at = datetime.now(timezone.utc)


def hold_if_required(session: Session, membership: TenantMembership) -> bool:
    if required(session, membership.tenant_id, membership.customer_id):
        hold(membership)
        session.flush()
        return True
    return False


def is_waiting(membership: TenantMembership | None) -> bool:
    return membership is not None and membership.pending_approval_at is not None


def waiting(session: Session, tenant_id: uuid.UUID) -> list[Waiting]:
    rows = session.execute(
        select(TenantMembership, User.email, Customer, SecurityGroup.name)
        .join(User, User.id == TenantMembership.user_id)
        .join(Customer, Customer.id == TenantMembership.customer_id)
        .outerjoin(SecurityGroup, SecurityGroup.id == TenantMembership.security_group_id)
        .where(TenantMembership.tenant_id == tenant_id, TenantMembership.pending_approval_at.is_not(None))
        .order_by(TenantMembership.pending_approval_at)
    ).all()
    return [Waiting(m, email, customer, role, m.pending_approval_at) for m, email, customer, role in rows]


def waiting_count(session: Session, tenant_id: uuid.UUID) -> int:
    from sqlalchemy import func

    return session.execute(select(func.count()).select_from(TenantMembership).where(
        TenantMembership.tenant_id == tenant_id, TenantMembership.pending_approval_at.is_not(None))).scalar_one()


def _waiting_one(session: Session, tenant_id: uuid.UUID, membership_id: uuid.UUID) -> TenantMembership:
    membership = session.get(TenantMembership, membership_id)
    if membership is None or membership.tenant_id != tenant_id or membership.pending_approval_at is None:
        raise ApprovalError("No one is waiting for approval there.")
    return membership


def approve(session: Session, tenant_id: uuid.UUID, membership_id: uuid.UUID) -> TenantMembership:
    membership = _waiting_one(session, tenant_id, membership_id)
    membership.is_active, membership.pending_approval_at = True, None
    session.flush()
    return membership


def reject(session: Session, tenant_id: uuid.UUID, membership_id: uuid.UUID, reason: str) -> TenantMembership:
    """Removes the waiting membership (and its account limits). The person keeps any other
    membership they have; a new invitation or sign-on would wait again."""
    reason = (reason or "").strip()
    if not reason:
        raise ApprovalError("Say why, for the customer's administrators.")
    membership = _waiting_one(session, tenant_id, membership_id)
    session.query(MembershipAccount).filter(MembershipAccount.membership_id == membership.id).delete()
    session.delete(membership)
    session.flush()
    return membership


def tell_administrators(session: Session, tenant_id: uuid.UUID, customer_id: uuid.UUID, *, email: str, approved: bool,
                        reason: str = "") -> int:
    """Notify the customer's administrators of the bank's decision. Returns how many."""
    from openclear.domain.notification import NotificationType
    from openclear.services import customer_people_service, notification_service

    told = 0
    tenant = session.get(Tenant, tenant_id)
    for membership in customer_people_service.administrators(session, tenant_id, customer_id):
        admin = session.get(User, membership.user_id)
        if admin is None or not admin.is_active:
            continue
        notification_service._queue(
            session, tenant_id=tenant_id, recipient=admin, notification_type=NotificationType.CUSTOMER_USER_DECIDED,
            template_context={"email": email, "approved": approved, "reason": reason, "bank": tenant.name if tenant else "Your bank"},
            resource_type="customer", resource_id=customer_id,
        )
        told += 1
    return told
