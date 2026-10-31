# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Deactivating and removing inactive access, as each bank chooses (Settings → Lockout and
inactive accounts; Tenant.deactivate_inactive, remove_inactive), daily with housekeeping.

- **Inactive** means no sign-in to this bank for so many days, counted from the membership's
  `last_active_at` (its last sign-in here, or when an administrator reactivated it), or from
  when it was made if it was never used.
- **Deactivate** turns the membership off, as an administrator's Deactivate does: the person
  can't sign in to this bank, and an administrator can turn it back on.
- **Remove** deletes the membership and its account limits: the person no longer belongs to
  this bank. The person (their identity, and their name on everything they did) is kept, as the
  action log and decisions refer to it.
- A week before either, the person is told by email (and SMS, as they choose).

Never touched: API clients, the platform's staff, and memberships waiting for the bank's
approval. The bank is never left without an active administrator who can manage users: if every
one is inactive, the most recently active is kept, and the action log says so."""

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from openclear.errors import UserFacingError
from openclear.domain.tenant import Tenant
from openclear.domain.tenant_membership import MembershipAccount, TenantMembership

WARN_DAYS = 7
MIN_DAYS = 14
USER_ADMIN = "user:manage"


class InactivityError(UserFacingError):
    """The message is fit to show."""


@dataclass
class Swept:
    warned: list[str] = field(default_factory=list)
    deactivated: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    kept_last_admin: str | None = None


def _aware(value: datetime | None) -> datetime | None:
    return None if value is None else (value if value.tzinfo else value.replace(tzinfo=timezone.utc))


def last_active(membership: TenantMembership) -> datetime | None:
    return _aware(membership.last_active_at) or _aware(membership.created_at)


def validate(*, deactivate: bool, deactivate_days: int, remove: bool, remove_days: int) -> None:
    if deactivate and deactivate_days < MIN_DAYS:
        raise InactivityError(f"Deactivate after at least {MIN_DAYS} days.")
    if remove and remove_days < MIN_DAYS:
        raise InactivityError(f"Remove after at least {MIN_DAYS} days.")
    if deactivate and remove and remove_days <= deactivate_days:
        raise InactivityError("Remove after more days than you deactivate after.")


def _people(session: Session, tenant_id: uuid.UUID) -> list[tuple]:
    """(membership, user, permissions) for each of the bank's people the rules can touch."""
    from openclear.domain.security_group import SecurityGroup
    from openclear.domain.user import User

    rows = session.execute(
        select(TenantMembership, User, SecurityGroup.permissions).join(User, User.id == TenantMembership.user_id)
        .outerjoin(SecurityGroup, SecurityGroup.id == TenantMembership.security_group_id)
        .where(TenantMembership.tenant_id == tenant_id, TenantMembership.pending_approval_at.is_(None),
               User.is_service_account.is_(False), User.platform_role.is_(None))).all()
    return [(m, u, set(p or [])) for m, u, p in rows]


def sweep(session: Session, tenant: Tenant, now: datetime | None = None) -> Swept:
    """Apply the bank's rules once: warn, deactivate, remove. Records each in the action log;
    the caller commits."""
    from openclear.services import audit_log_service, notification_service
    from openclear.domain.notification import NotificationType

    now = now or datetime.now(timezone.utc)
    done = Swept()
    if not (tenant.deactivate_inactive or tenant.remove_inactive):
        return done
    people = _people(session, tenant.id)
    deactivate_before = now - timedelta(days=tenant.deactivate_inactive_days) if tenant.deactivate_inactive else None
    remove_before = now - timedelta(days=tenant.remove_inactive_days) if tenant.remove_inactive else None

    def ends(m: TenantMembership) -> bool:
        last = last_active(m)
        return last is not None and ((remove_before is not None and last < remove_before)
                                     or (m.is_active and deactivate_before is not None and last < deactivate_before))

    # Keep one active administrator who can manage users, whatever happens to the rest.
    admins = [(m, u) for m, u, p in people if m.is_active and m.customer_id is None and USER_ADMIN in p]
    if admins and all(ends(m) for m, _u in admins):
        keep, kept_user = max(admins, key=lambda pair: last_active(pair[0]))
        done.kept_last_admin = kept_user.email
        audit_log_service.record_action(session, tenant.id, actor_user_id=None, channel="system", action="user.inactivity_kept",
                                        summary=f"Kept {kept_user.email}'s access although inactive: the bank's last active "
                                                "administrator who can manage users", resource_type="user", resource_id=kept_user.id)
    else:
        keep = None

    for membership, user, _permissions in people:
        if membership is keep:
            continue
        last = last_active(membership)
        if last is None:
            continue
        if remove_before is not None and last < remove_before:
            session.query(MembershipAccount).filter(MembershipAccount.membership_id == membership.id).delete()
            _end_embed_launches(session, membership.id)
            session.delete(membership)
            done.removed.append(user.email)
            audit_log_service.record_action(session, tenant.id, actor_user_id=None, channel="system", action="user.remove_inactive",
                                            summary=f"Removed {user.email}'s access: no sign-in for {tenant.remove_inactive_days} days",
                                            resource_type="user", resource_id=user.id)
            continue
        if membership.is_active and deactivate_before is not None and last < deactivate_before:
            membership.is_active = False
            done.deactivated.append(user.email)
            audit_log_service.record_action(session, tenant.id, actor_user_id=None, channel="system", action="user.deactivate",
                                            summary=f"Deactivated {user.email}'s access: no sign-in for {tenant.deactivate_inactive_days} days",
                                            resource_type="user", resource_id=user.id)
            continue
        # A week before the next step, once per stretch of inactivity.
        next_step = deactivate_before if (membership.is_active and deactivate_before is not None) else remove_before
        if next_step is None or not membership.is_active:
            continue
        warned = _aware(membership.inactivity_warned_at)
        if last < next_step + timedelta(days=WARN_DAYS) and (warned is None or warned < last):
            what = "turned off" if next_step is deactivate_before else "removed"
            days = (last - next_step).days
            notification_service._queue(
                session, tenant_id=tenant.id, recipient=user, notification_type=NotificationType.INACTIVITY_WARNING,
                template_context={"bank": tenant.name, "what": what, "days": max(days, 1), "slug": tenant.slug},
                resource_type="user", resource_id=user.id)
            membership.inactivity_warned_at = now
            done.warned.append(user.email)
    session.flush()
    return done


def _end_embed_launches(session: Session, membership_id: uuid.UUID) -> None:
    from openclear.domain.embed_launch import EmbedLaunch

    session.query(EmbedLaunch).filter(EmbedLaunch.membership_id == membership_id).delete()


def sweep_all(session: Session, now: datetime | None = None) -> int:
    """Daily: every active bank that has either rule on. Returns how many people's access ended."""
    from openclear.db.tenancy import bind_session_to_tenant

    ended = 0
    for tenant in session.execute(select(Tenant).where(Tenant.is_active.is_(True)).where(
            (Tenant.deactivate_inactive.is_(True)) | (Tenant.remove_inactive.is_(True)))).scalars().all():
        bind_session_to_tenant(session, tenant.id)
        done = sweep(session, tenant, now)
        session.commit()
        ended += len(done.deactivated) + len(done.removed)
    return ended
