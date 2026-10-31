# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Bank-granted support access (UX_PLAN.md U8; domain/support_access_grant.py), instead of
impersonation.

- A bank admin (tenant:manage) grants a named platform support person access, for a stated
  reason and period. Read-only by default; write access waits for a second bank admin.
- The support person opens the bank from the platform page and works under their own name,
  with a banner saying whose access it is and until when (auth/deps.py builds that session
  from the grant, not from a membership). They can't see secrets, change users, groups or
  settings, or decide items (auth/permissions.py SUPPORT_*_PERMISSIONS).
- Nobody grants or approves their own access. Revoking, or the period ending, ends the
  session on its next request.
- Every grant, approval, revocation, use and expiry is in the bank's action log, and so a
  security event."""

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from openclear.errors import UserFacingError
from openclear.auth.permissions import SUPPORT_READ_PERMISSIONS, SUPPORT_WRITE_PERMISSIONS
from openclear.db.tenancy import bind_session_to_tenant
from openclear.domain.support_access_grant import SupportAccessGrant
from openclear.domain.tenant import Tenant
from openclear.domain.user import User
from openclear.services import audit_log_service

MAX_HOURS = 72
PLATFORM_ROLES = ("operator", "support")


class SupportAccessError(UserFacingError):
    """Not allowed, or bad input; the message is fit to show."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime | None) -> datetime | None:
    return value.replace(tzinfo=timezone.utc) if value is not None and value.tzinfo is None else value


def permissions_for(grant: SupportAccessGrant) -> frozenset[str]:
    return SUPPORT_READ_PERMISSIONS | (SUPPORT_WRITE_PERMISSIONS if grant.write_access else frozenset())


def _record(session: Session, grant: SupportAccessGrant, actor_user_id: uuid.UUID | None, action: str, summary: str,
            channel: str = "web") -> None:
    audit_log_service.record_action(session, grant.tenant_id, actor_user_id=actor_user_id, channel=channel,
                                    action=f"support_access.{action}", summary=summary, resource_type="support_access_grant",
                                    resource_id=grant.id)


def support_people(session: Session) -> list[User]:
    """Who a bank can grant access to: active platform staff."""
    return list(session.execute(select(User).where(User.platform_role.in_(PLATFORM_ROLES), User.is_active.is_(True))
                                .order_by(User.email)).scalars())


def grant(session: Session, tenant_id: uuid.UUID, *, support_email: str, reason: str, hours: int, write_access: bool,
          granted_by_user_id: uuid.UUID) -> SupportAccessGrant:
    support = session.execute(select(User).where(User.email == (support_email or "").strip().lower())).scalar_one_or_none()
    if support is None or support.platform_role not in PLATFORM_ROLES or not support.is_active:
        raise SupportAccessError("That isn't a platform support person.")
    if support.id == granted_by_user_id:
        raise SupportAccessError("You can't grant access to yourself.")
    reason = (reason or "").strip()
    if len(reason) < 5:
        raise SupportAccessError("Say why access is needed.")
    if not 1 <= hours <= MAX_HOURS:
        raise SupportAccessError(f"Access lasts between 1 and {MAX_HOURS} hours.")
    now = _now()
    row = SupportAccessGrant(
        tenant_id=tenant_id, support_user_id=support.id, reason=reason[:500], write_access=write_access,
        status="pending" if write_access else "active", granted_by_user_id=granted_by_user_id,
        starts_at=None if write_access else now, expires_at=now + timedelta(hours=hours),
    )
    session.add(row)
    session.flush()
    _record(session, row, granted_by_user_id, "grant",
            f"Granted {support.email} {'read and write' if write_access else 'read-only'} support access for {hours} hours"
            f"{' (waiting for a second admin)' if write_access else ''}: {row.reason}")
    return row


def _get(session: Session, tenant_id: uuid.UUID, grant_id: uuid.UUID) -> SupportAccessGrant:
    row = session.get(SupportAccessGrant, grant_id)
    if row is None or row.tenant_id != tenant_id:
        raise SupportAccessError("That grant doesn't exist.")
    return row


def approve(session: Session, tenant_id: uuid.UUID, grant_id: uuid.UUID, *, approver_user_id: uuid.UUID) -> SupportAccessGrant:
    row = _get(session, tenant_id, grant_id)
    expire_due(session, tenant_id)
    if row.status != "pending":
        raise SupportAccessError("Only a grant waiting for approval can be approved.")
    if approver_user_id in (row.granted_by_user_id, row.support_user_id):
        raise SupportAccessError("A second admin, not the one who granted it, must approve write access.")
    row.status, row.approved_by_user_id, row.starts_at = "active", approver_user_id, _now()
    session.flush()
    _record(session, row, approver_user_id, "approve", "Approved write access for support")
    return row


def revoke(session: Session, tenant_id: uuid.UUID, grant_id: uuid.UUID, *, user_id: uuid.UUID) -> SupportAccessGrant:
    row = _get(session, tenant_id, grant_id)
    if row.status not in ("pending", "active"):
        raise SupportAccessError("That grant has already ended.")
    row.status, row.ended_at, row.ended_by_user_id = "revoked", _now(), user_id
    session.flush()
    _record(session, row, user_id, "revoke", "Ended support access")
    return row


def expire_due(session: Session, tenant_id: uuid.UUID | None = None, *, now: datetime | None = None) -> int:
    """Marks grants past their period expired, each with an action-log entry. Called when
    grants are listed or used, and by the disposition sweep. The caller commits."""
    now = now or _now()
    stmt = select(SupportAccessGrant).where(SupportAccessGrant.status.in_(("pending", "active")),
                                            SupportAccessGrant.expires_at <= now)
    if tenant_id is not None:
        stmt = stmt.where(SupportAccessGrant.tenant_id == tenant_id)
    rows = list(session.execute(stmt).scalars())
    for row in rows:
        bind_session_to_tenant(session, row.tenant_id)
        row.status, row.ended_at = "expired", now
        _record(session, row, None, "expire", "Support access ended at the end of its period", channel="system")
    session.flush()
    return len(rows)


def for_bank(session: Session, tenant_id: uuid.UUID) -> list[SupportAccessGrant]:
    expire_due(session, tenant_id)
    return list(session.execute(select(SupportAccessGrant).where(SupportAccessGrant.tenant_id == tenant_id)
                                .order_by(SupportAccessGrant.granted_at.desc())).scalars())


def for_support_person(session: Session, user_id: uuid.UUID) -> list[tuple[SupportAccessGrant, Tenant]]:
    """The grants made to this person, newest first, with each bank."""
    expire_due(session)
    return list(session.execute(select(SupportAccessGrant, Tenant).join(Tenant, Tenant.id == SupportAccessGrant.tenant_id)
                                .where(SupportAccessGrant.support_user_id == user_id)
                                .order_by(SupportAccessGrant.granted_at.desc())).all())


def active_grant(session: Session, grant_id: uuid.UUID, *, user_id: uuid.UUID, tenant_id: uuid.UUID | None = None) -> SupportAccessGrant | None:
    """The grant, if it's active, unexpired and made to this person (and this bank)."""
    row = session.get(SupportAccessGrant, grant_id)
    if row is None or row.support_user_id != user_id or (tenant_id is not None and row.tenant_id != tenant_id):
        return None
    if row.status != "active" or _aware(row.expires_at) <= _now():
        return None
    return row


def start(session: Session, grant_id: uuid.UUID, *, user: User) -> SupportAccessGrant:
    """The support person opens the bank: recorded as a use. Raises SupportAccessError."""
    expire_due(session)
    row = active_grant(session, grant_id, user_id=user.id)
    if row is None or user.platform_role not in PLATFORM_ROLES:
        raise SupportAccessError("That access isn't active.")
    bind_session_to_tenant(session, row.tenant_id)
    row.last_used_at = _now()
    _record(session, row, user.id, "use", f"{user.email} opened the organization with support access")
    return row


def set_platform_role(session: Session, email: str, role: str | None, *, password: str | None = None) -> tuple[User, bool]:
    """Makes someone platform staff ("operator" or "support"), or (None) not. A person new to
    OpenClear is created with `password`. Returns (the user, whether they were created).
    Only scripts/platform_staff.py calls this: no screen can. The caller commits."""
    from openclear.auth.security import hash_password

    if role not in (*PLATFORM_ROLES, None):
        raise SupportAccessError(f"The role is one of {', '.join(PLATFORM_ROLES)}.")
    email = (email or "").strip().lower()
    user = session.execute(select(User).where(User.email == email)).scalar_one_or_none()
    created = False
    if user is None:
        if role is None:
            raise SupportAccessError("No one signs in with that email.")
        if not password or len(password) < 12:
            raise SupportAccessError("A new person needs a password of at least 12 characters.")
        user = User(email=email, hashed_password=hash_password(password))
        session.add(user)
        created = True
    if not created and user.platform_role != role:
        user.token_version += 1  # a change of role signs them out everywhere
    user.platform_role = role
    session.flush()
    return user, created
