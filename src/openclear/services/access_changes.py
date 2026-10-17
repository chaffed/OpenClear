# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""People's access through dual control (DUAL_CONTROL_PLAN.md DC2): when the bank's "Access
changes need a second admin" is on, a new person, a reactivated one, a group with more
permissions, a wider customer scope or more accounts wait for another person who manages users.
Removing or narrowing access never waits.

A new membership waits as one waiting for the bank's approval of new customer users does
(`pending_approval_at`): it can't sign in, and says so; approving the change request turns it
on."""

import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from openclear.domain.tenant_membership import TenantMembership
from openclear.services import change_request_service, security_group_service


def waits(session: Session, tenant_id: uuid.UUID) -> bool:
    return change_request_service.needs_approval(session, tenant_id, "access.membership")


def hold_new(session: Session, tenant_id: uuid.UUID, memberships: list[TenantMembership], *, user_id: uuid.UUID,
             channel: str = "web") -> list:
    """Make freshly granted memberships wait for a second person, if the bank asks. Returns the
    outcomes (empty when it doesn't)."""
    if not memberships or not waits(session, tenant_id):
        return []
    outcomes = []
    for membership in memberships:
        if membership.pending_approval_at is not None:
            continue  # already waiting (the bank's approval of a customer's new people)
        membership.is_active, membership.pending_approval_at = False, datetime.now(timezone.utc)
        session.flush()
        group = security_group_service.get_security_group(session, tenant_id, membership.security_group_id)
        outcomes.append(change_request_service.request_or_apply(
            session, "access.membership", {"membership_id": str(membership.id)}, tenant_id=tenant_id, user_id=user_id, channel=channel,
            summary=f"Give {_email(session, membership)} access as {group.name if group else 'a member'}"))
    return outcomes


def change_entitlements(session: Session, tenant_id: uuid.UUID, membership: TenantMembership, *, security_group_id: uuid.UUID,
                        customer_id: uuid.UUID | None, user_id: uuid.UUID, channel: str = "web"):
    group = security_group_service.get_security_group(session, tenant_id, security_group_id)
    if group is None:
        raise ValueError("Security group not found")
    return change_request_service.request_or_apply(
        session, "access.entitlements", {"membership_id": str(membership.id), "security_group_id": str(security_group_id),
                                         "customer_id": str(customer_id) if customer_id else None, "permissions": sorted(group.permissions or [])},
        tenant_id=tenant_id, user_id=user_id, channel=channel, summary=f"Make {_email(session, membership)} {group.name}")


def _email(session: Session, membership: TenantMembership) -> str:
    from openclear.domain.user import User

    user = session.get(User, membership.user_id)
    return user.email if user else "someone"


def message(outcomes: list) -> str | None:
    """What the person who granted the access sees, when it waits."""
    waiting = [o for o in outcomes if o.waiting]
    if not waiting:
        return None
    if any(o.change.applies_at for o in waiting):
        return "Nobody else can approve this access, so it starts in 24 hours unless you withdraw it (Settings → Changes awaiting approval)."
    return "The access starts when a second person who manages users approves it (Settings → Changes awaiting approval)."


def membership_ids(session: Session, tenant_id: uuid.UUID) -> set[uuid.UUID]:
    """The bank's memberships now, so a route can tell which ones it just made (`made_since`)."""
    from sqlalchemy import select

    return set(session.execute(select(TenantMembership.id).where(TenantMembership.tenant_id == tenant_id)).scalars())


def made_since(session: Session, tenant_id: uuid.UUID, before: set[uuid.UUID]) -> list[TenantMembership]:
    """People's memberships made since `before` (API clients' aren't people)."""
    from sqlalchemy import select

    from openclear.domain.user import User

    rows = session.execute(select(TenantMembership).join(User, User.id == TenantMembership.user_id).where(
        TenantMembership.tenant_id == tenant_id, User.is_service_account.is_(False))).scalars()
    return [m for m in rows if m.id not in before]
