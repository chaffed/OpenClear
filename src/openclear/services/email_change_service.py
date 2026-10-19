# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed
"""Changing the email address someone signs in with (OWASP_PLAN.md OW-D31; ADR 0006).

The person is their id; the email is what they type to sign in, and it can change by four
routes: the person themselves and an administrator, both confirmed by a link to the new
address (request_change, confirm); the bank's directory (SCIM) and its sign-on, applied
directly (apply_change). Every route ends in apply_change, which emails both the old and the
new address and records `user.email.changed`."""

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from openclear.domain.email_change import EmailChange
from openclear.domain.notification import NotificationType
from openclear.domain.tenant import Tenant
from openclear.domain.user import User

CONFIRM_HOURS = 24


def looks_like_email(email: str) -> bool:
    """name@domain.tld, with no spaces: checked by splitting rather than a regex, which took
    time in proportion to the square of the length (CODE_SCANNING_PLAN.md CS1)."""
    if not email or len(email) > 320 or any(ch.isspace() for ch in email) or email.count("@") != 1:
        return False
    local, domain = email.split("@")
    labels = domain.split(".")
    return bool(local) and len(labels) >= 2 and labels[0] != "" and labels[-1] != ""


class EmailChangeError(ValueError):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def normalize(email: str) -> str:
    email = (email or "").strip().lower()
    if not looks_like_email(email):
        raise EmailChangeError("Enter an email address such as name@example.com.")
    return email


def check_available(session: Session, user: User, new_email: str) -> str:
    """The new address, normalized, if this person may move to it."""
    new_email = normalize(new_email)
    if new_email == user.email.lower():
        raise EmailChangeError("That's already the address.")
    taken = session.execute(select(User.id).where(func.lower(User.email) == new_email, User.id != user.id)).first()
    if taken:
        raise EmailChangeError("Someone else already signs in with that address.")
    return new_email


def apply_change(session: Session, user: User, new_email: str, *, tenant_id: uuid.UUID | None, actor_user_id: uuid.UUID | None,
                 channel: str, via: str) -> None:
    """Moves the person to the new address, emailing both and recording it (against `tenant_id`'s
    action log when there is one). `via`: self, admin, directory, sso or domain_move."""
    from openclear.services import audit_log_service, notification_service

    new_email = check_available(session, user, new_email)
    old_email = user.email
    user.email = new_email
    session.flush()
    bank_name = (session.get(Tenant, tenant_id).name if tenant_id else None) or "your bank"
    context = {"old_email": old_email, "new_email": new_email, "via": via, "bank_name": bank_name}
    for destination in (old_email, new_email):
        notification_service.notify_email_address(session, user, destination=destination, notification_type=NotificationType.EMAIL_CHANGED,
                                                  context=context, tenant_id=tenant_id)
    if tenant_id is not None:
        audit_log_service.record_action(
            session, tenant_id, actor_user_id=actor_user_id, channel=channel, action="user.email.changed",
            summary=f"Email changed from {old_email} to {new_email}", resource_type="user", resource_id=user.id,
            metadata={"via": via, "old_email": old_email, "new_email": new_email},
        )


def request_change(session: Session, user: User, new_email: str, *, tenant_id: uuid.UUID, requested_by_user_id: uuid.UUID,
                   base_url: str, channel: str = "web") -> EmailChange:
    """Emails a link to the new address; the change waits for it. A newer request replaces an
    older one."""
    from openclear.services import audit_log_service, notification_service

    new_email = check_available(session, user, new_email)
    now = _now()
    session.execute(update(EmailChange).where(EmailChange.user_id == user.id, EmailChange.used_at.is_(None)).values(used_at=now))
    token = secrets.token_urlsafe(32)
    row = EmailChange(user_id=user.id, tenant_id=tenant_id, new_email=new_email, token_hash=_hash(token),
                      requested_by_user_id=requested_by_user_id, expires_at=now + timedelta(hours=CONFIRM_HOURS))
    session.add(row)
    session.flush()
    by_admin = requested_by_user_id != user.id
    notification_service.notify_email_address(
        session, user, destination=new_email, notification_type=NotificationType.EMAIL_CHANGE_CONFIRM, tenant_id=tenant_id,
        context={"link": f"{base_url.rstrip('/')}/ui/email-change/{token}", "old_email": user.email, "hours": CONFIRM_HOURS,
                 "by_admin": by_admin, "bank_name": session.get(Tenant, tenant_id).name})
    audit_log_service.record_action(
        session, tenant_id, actor_user_id=requested_by_user_id, channel=channel, action="user.email.change_requested",
        summary=f"Asked to change {user.email}'s email to {new_email}; waiting for the new address to confirm",
        resource_type="user", resource_id=user.id, metadata={"via": "admin" if by_admin else "self"},
    )
    return row


@dataclass
class OpenedChange:
    change: EmailChange
    user: User


def open_change(session: Session, token: str) -> OpenedChange | None:
    """The change behind a link, if it can still be used. Binds the session to the bank that
    asked, for its action log."""
    from openclear.db.tenancy import bind_session_to_tenant

    row = session.execute(select(EmailChange).where(EmailChange.token_hash == _hash(token or ""))).scalar_one_or_none()
    if row is None or row.used_at is not None or row.expires_at.replace(tzinfo=row.expires_at.tzinfo or timezone.utc) <= _now():
        return None
    if row.tenant_id is not None:
        bind_session_to_tenant(session, row.tenant_id)
    return OpenedChange(change=row, user=session.get(User, row.user_id))


def confirm(session: Session, opened: OpenedChange) -> None:
    change, user = opened.change, opened.user
    change.used_at = _now()
    apply_change(session, user, change.new_email, tenant_id=change.tenant_id, actor_user_id=change.requested_by_user_id,
                 channel="web", via="admin" if change.requested_by_user_id != user.id else "self")


def pending(session: Session, user_id: uuid.UUID) -> EmailChange | None:
    return session.execute(select(EmailChange).where(
        EmailChange.user_id == user_id, EmailChange.used_at.is_(None), EmailChange.expires_at > _now())
        .order_by(EmailChange.created_at.desc()).limit(1)).scalar_one_or_none()


def request_for_member(session: Session, tenant_id: uuid.UUID, membership_id: uuid.UUID, new_email: str, *,
                       customer_id: uuid.UUID | None, actor_user_id: uuid.UUID, base_url: str) -> tuple[User, EmailChange]:
    """An administrator's request for someone in their organization: the bank's (customer_id
    None) or a customer's. Only for someone who belongs nowhere else, since the address is
    theirs across every bank; never for the administrator themselves, a system or the demo."""
    from openclear.db.tenancy import memberships_elsewhere
    from openclear.repositories.tenant_membership_repo import TenantMembershipRepository

    tenant = session.get(Tenant, tenant_id)
    if tenant is not None and tenant.is_demo:
        raise EmailChangeError("Email addresses can't be changed in the demo organization.")
    membership = TenantMembershipRepository(session, tenant_id).get(membership_id)
    if membership is None or (customer_id is not None and membership.customer_id != customer_id):
        raise EmailChangeError("Person not found.")
    user = session.get(User, membership.user_id)
    if user is None or user.is_service_account:
        raise EmailChangeError("Person not found.")
    if user.id == actor_user_id:
        raise EmailChangeError("To change your own address, use Security → Email address.")
    if memberships_elsewhere(session, user.id, tenant_id, customer_id=customer_id, within_customer=customer_id is not None):
        raise EmailChangeError(f"{user.email} also belongs to other organizations, so only they can change their address.")
    return user, request_change(session, user, new_email, tenant_id=tenant_id, requested_by_user_id=actor_user_id, base_url=base_url)


# --- Moving everyone to a new domain (OWASP_PLAN.md OW-D33) -----------------------------------


def _clean_domain(domain: str) -> str:
    return (domain or "").strip().lower().rstrip(".").removeprefix("@")


def domain_move_people(session: Session, tenant_id: uuid.UUID, customer_id: uuid.UUID | None, from_domain: str) -> list[User]:
    """Everyone in this organization (a customer's people, or the bank's own staff) whose
    address is at `from_domain`."""
    from openclear.domain.tenant_membership import TenantMembership

    owner = TenantMembership.customer_id.is_(None) if customer_id is None else TenantMembership.customer_id == customer_id
    return list(session.execute(select(User).join(TenantMembership, TenantMembership.user_id == User.id).where(
        TenantMembership.tenant_id == tenant_id, owner, User.is_service_account.is_(False),
        func.lower(User.email).like(f"%@{_clean_domain(from_domain)}")).distinct().order_by(User.email)).scalars())


def check_domain_move(session: Session, tenant_id: uuid.UUID, customer_id: uuid.UUID | None, from_domain: str, to_domain: str) -> int:
    """How many people would move; raises EmailChangeError if the move can't be asked for."""
    from openclear.services import customer_sign_on_service

    from_domain, to_domain = _clean_domain(from_domain), _clean_domain(to_domain)
    if not from_domain or not to_domain or from_domain == to_domain:
        raise EmailChangeError("Choose the old domain and a different new one.")
    if not customer_sign_on_service.verified_domain(session, tenant_id, customer_id, f"x@{to_domain}"):
        raise EmailChangeError(f"{to_domain} isn't one of your verified domains yet: add it and verify it first.")
    count = len(domain_move_people(session, tenant_id, customer_id, from_domain))
    if not count:
        raise EmailChangeError(f"Nobody here has an address at {from_domain}.")
    return count


def move_domain(session: Session, tenant_id: uuid.UUID, customer_id: uuid.UUID | None, from_domain: str, to_domain: str, *,
                actor_user_id: uuid.UUID | None, base_url: str) -> str:
    """Moves everyone at `from_domain` to the same name at `to_domain`. Someone who also belongs
    to another organization isn't moved: they're sent a link to confirm it themselves. Someone
    whose new address is taken is left as they are. Returns what was done."""
    from openclear.db.tenancy import memberships_elsewhere

    check_domain_move(session, tenant_id, customer_id, from_domain, to_domain)
    to_domain = _clean_domain(to_domain)
    moved, asked, left = 0, [], []
    for user in domain_move_people(session, tenant_id, customer_id, from_domain):
        new_email = f"{user.email.rsplit('@', 1)[0]}@{to_domain}"
        try:
            if memberships_elsewhere(session, user.id, tenant_id, customer_id=customer_id, within_customer=customer_id is not None):
                request_change(session, user, new_email, tenant_id=tenant_id, requested_by_user_id=actor_user_id or user.id,
                               base_url=base_url)
                asked.append(user.email)
            else:
                apply_change(session, user, new_email, tenant_id=tenant_id, actor_user_id=actor_user_id, channel="web", via="domain_move")
                moved += 1
        except EmailChangeError:
            left.append(user.email)
    done = f"Moved {moved} {'person' if moved == 1 else 'people'} to {to_domain}"
    if asked:
        done += f"; sent a link to confirm to {', '.join(asked)}, who also belong to other organizations"
    if left:
        done += f"; left {', '.join(left)}, whose new address is taken"
    return done[:2000]
