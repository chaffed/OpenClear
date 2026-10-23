# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Incident contacts and notices (REGULATORY_PLAN.md RG3; docs/compliance/incident-notification.md).

A bank names who hears of an incident at OpenClear's host (12 CFR 304.24(a)(1)), with its CEO and
CIO as the fallback the rule gives (304.24(a)(2)). The installation's operator issues a notice to
the banks an incident affects (or announces maintenance beforehand, 304.24(b)): it's emailed to
each bank's contacts, always, and shown to its administrators until one acknowledges it. Sending
and acknowledging are in the bank's action log and raised as security events, so the host has
evidence that it told the bank, and the bank of when it learned (its own clocks: 36 hours to the
FDIC, 72 to New York DFS)."""

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from email_validator import EmailNotValidError, validate_email
from sqlalchemy import select
from sqlalchemy.orm import Session

from openclear.db.tenancy import bind_session_to_tenant
from openclear.domain.incident import IncidentContact, IncidentNotice, IncidentNoticeDelivery
from openclear.domain.tenant import Tenant
from openclear.services import audit_log_service

KINDS = {"incident": "Incident", "maintenance": "Scheduled maintenance", "update": "Update", "closed": "Closed"}
CONTACT_KINDS = ("contact", "ceo", "cio")
MAX_CONTACTS = 10


class IncidentError(ValueError):
    """Fit to show."""


@dataclass(frozen=True)
class ContactInput:
    kind: str
    name: str
    email: str
    role: str | None = None
    phone: str | None = None


# --- Contacts ------------------------------------------------------------------------------------


def contacts(session: Session, tenant_id: uuid.UUID) -> list[IncidentContact]:
    order = {kind: n for n, kind in enumerate(CONTACT_KINDS)}
    rows = session.execute(select(IncidentContact).where(IncidentContact.tenant_id == tenant_id)).scalars().all()
    return sorted(rows, key=lambda c: (order.get(c.kind, 9), c.created_at or datetime.min.replace(tzinfo=timezone.utc), c.name))


def _clean(contact: ContactInput) -> ContactInput:
    if contact.kind not in CONTACT_KINDS:
        raise IncidentError("Choose whether this is an incident contact, the CEO or the CIO.")
    name = (contact.name or "").strip()
    if not name:
        raise IncidentError("Give each contact a name.")
    try:
        email = validate_email((contact.email or "").strip(), check_deliverability=False).normalized
    except EmailNotValidError:
        raise IncidentError(f"{contact.email!r} isn't an email address.") from None
    return ContactInput(contact.kind, name[:255], email, (contact.role or "").strip()[:255] or None, (contact.phone or "").strip()[:40] or None)


def set_contacts(session: Session, tenant_id: uuid.UUID, wanted: list[ContactInput], *, user_id: uuid.UUID | None,
                 channel: str = "web") -> list[IncidentContact]:
    """Replaces the bank's contacts and confirms them. Audited as incident_contact.changed (a high
    security event: whoever sets these decides where incident notices go), naming the old ones too."""
    cleaned = [_clean(c) for c in wanted if (c.name or c.email)]
    if not [c for c in cleaned if c.kind == "contact"]:
        raise IncidentError("Name at least one incident contact.")
    if len(cleaned) > MAX_CONTACTS:
        raise IncidentError(f"At most {MAX_CONTACTS} contacts.")
    before = [f"{c.kind}: {c.name} <{c.email}>" for c in contacts(session, tenant_id)]
    for old in contacts(session, tenant_id):
        session.delete(old)
    session.flush()
    rows = [IncidentContact(tenant_id=tenant_id, kind=c.kind, name=c.name, email=c.email, role=c.role, phone=c.phone) for c in cleaned]
    session.add_all(rows)
    session.get(Tenant, tenant_id).incident_contacts_confirmed_at = datetime.now(timezone.utc)
    session.flush()
    after = [f"{c.kind}: {c.name} <{c.email}>" for c in rows]
    audit_log_service.record_action(
        session, tenant_id, actor_user_id=user_id, channel=channel, action="incident_contact.changed",
        summary=f"Incident contacts set: {', '.join(after)}"[:500], resource_type="tenant", resource_id=tenant_id,
        metadata={"before": before, "after": after})
    return rows


def confirm_contacts(session: Session, tenant_id: uuid.UUID, *, user_id: uuid.UUID | None) -> None:
    """The yearly confirmation that the contacts are still right."""
    if not [c for c in contacts(session, tenant_id) if c.kind == "contact"]:
        raise IncidentError("Name at least one incident contact first.")
    session.get(Tenant, tenant_id).incident_contacts_confirmed_at = datetime.now(timezone.utc)
    audit_log_service.record_action(session, tenant_id, actor_user_id=user_id, channel="web", action="incident_contact.confirmed",
                                    summary="Confirmed the incident contacts", resource_type="tenant", resource_id=tenant_id)


def recipients(session: Session, tenant_id: uuid.UUID) -> list[str]:
    """Where a notice goes: the bank's incident contacts; with none, its CEO and CIO
    (304.24(a)(2)); with neither, its administrators, so a notice always reaches someone."""
    from openclear.services.notification_service import _recipients_for_permission

    rows = contacts(session, tenant_id)
    for kinds in (("contact",), ("ceo", "cio")):
        found = sorted({c.email for c in rows if c.kind in kinds})
        if found:
            return found
    return sorted({u.email for u in _recipients_for_permission(session, tenant_id, "admin:manage", customer_id=None)})


# --- Notices -------------------------------------------------------------------------------------


def issue(session: Session, *, kind: str, title: str, message: str, tenant_ids: list[uuid.UUID], user_id: uuid.UUID | None,
          started_at: datetime | None = None, determined_at: datetime | None = None, services_affected: str | None = None,
          bank_data_affected: bool = False, follows_id: uuid.UUID | None = None) -> IncidentNotice:
    """Issues a notice to each bank in `tenant_ids` at once: no second operator first, since
    speed is the point; it's in the platform's action log for review. Commits nothing."""
    from openclear.services import platform_log_service

    if kind not in KINDS:
        raise IncidentError("Choose the kind of notice.")
    title, message = (title or "").strip(), (message or "").strip()
    if not title or not message:
        raise IncidentError("A notice needs a title and a message.")
    banks = [t for t in (session.get(Tenant, tid) for tid in dict.fromkeys(tenant_ids)) if t is not None]
    if not banks:
        raise IncidentError("Choose the banks it affects.")
    if follows_id is not None and session.get(IncidentNotice, follows_id) is None:
        raise IncidentError("The notice this follows doesn't exist.")
    notice = IncidentNotice(kind=kind, title=title[:200], message=message, started_at=started_at, determined_at=determined_at,
                            services_affected=(services_affected or "").strip()[:500] or None, bank_data_affected=bank_data_affected,
                            follows_id=follows_id, issued_by_user_id=user_id, issued_at=datetime.now(timezone.utc))
    session.add(notice)
    session.flush()
    for bank in banks:
        _deliver(session, notice, bank, user_id)
    bind_session_to_tenant(session, None)
    platform_log_service.record(session, actor_user_id=user_id, action="platform.incident_notice.issue",
                                summary=f"{KINDS[kind]} notice {title!r} to {len(banks)} bank{'s' if len(banks) != 1 else ''}"[:500],
                                resource_type="incident_notice", resource_id=notice.id)
    return notice


def _deliver(session: Session, notice: IncidentNotice, bank: Tenant, user_id: uuid.UUID | None) -> IncidentNoticeDelivery:
    from openclear.domain.notification import Notification, NotificationChannel, NotificationType
    from openclear.services.notification_service import _render_body

    bind_session_to_tenant(session, bank.id)
    to = recipients(session, bank.id)
    delivery = IncidentNoticeDelivery(tenant_id=bank.id, notice_id=notice.id, recipients=to, sent_at=datetime.now(timezone.utc))
    session.add(delivery)
    session.flush()
    context = {"notice": notice, "kind": KINDS[notice.kind], "bank_name": bank.name}
    for address in to:
        # Always email, whatever anyone's notification settings: it's the bank's notice under 304.24.
        session.add(Notification(
            tenant_id=bank.id, recipient_user_id=None, notification_type=NotificationType.INCIDENT_NOTICE,
            channel=NotificationChannel.EMAIL, destination=address, subject=f"[{KINDS[notice.kind]}] {notice.title}"[:255],
            body=_render_body(NotificationType.INCIDENT_NOTICE, NotificationChannel.EMAIL, context),
            resource_type="incident_notice_delivery", resource_id=delivery.id))
    audit_log_service.record_action(
        session, bank.id, actor_user_id=user_id, channel="system", action="incident.notice.sent",
        summary=f"{KINDS[notice.kind]} notice from the service provider: {notice.title} (emailed to {', '.join(to) or 'nobody'})"[:500],
        resource_type="incident_notice_delivery", resource_id=delivery.id,
        metadata={"notice_id": str(notice.id), "kind": notice.kind, "recipients": to})
    session.flush()
    return delivery


def unacknowledged(session: Session, tenant_id: uuid.UUID) -> list[tuple[IncidentNoticeDelivery, IncidentNotice]]:
    """A bank's notices nobody there has acknowledged yet: its administrators see them on every page."""
    return list(session.execute(select(IncidentNoticeDelivery, IncidentNotice)
                                .join(IncidentNotice, IncidentNotice.id == IncidentNoticeDelivery.notice_id)
                                .where(IncidentNoticeDelivery.tenant_id == tenant_id, IncidentNoticeDelivery.acknowledged_at.is_(None))
                                .order_by(IncidentNoticeDelivery.sent_at)).all())


def acknowledge(session: Session, tenant_id: uuid.UUID, delivery_id: uuid.UUID, *, user_id: uuid.UUID) -> IncidentNoticeDelivery:
    delivery = session.get(IncidentNoticeDelivery, delivery_id)
    if delivery is None or delivery.tenant_id != tenant_id:
        raise IncidentError("No such notice.")
    if delivery.acknowledged_at is None:
        delivery.acknowledged_at, delivery.acknowledged_by_user_id = datetime.now(timezone.utc), user_id
        notice = session.get(IncidentNotice, delivery.notice_id)
        audit_log_service.record_action(session, tenant_id, actor_user_id=user_id, channel="web", action="incident.notice.acknowledged",
                                        summary=f"Acknowledged the service provider's notice: {notice.title}"[:500],
                                        resource_type="incident_notice_delivery", resource_id=delivery.id)
        session.flush()
    return delivery


def history(session: Session, tenant_id: uuid.UUID, limit: int = 100) -> list[tuple[IncidentNoticeDelivery, IncidentNotice]]:
    return list(session.execute(select(IncidentNoticeDelivery, IncidentNotice)
                                .join(IncidentNotice, IncidentNotice.id == IncidentNoticeDelivery.notice_id)
                                .where(IncidentNoticeDelivery.tenant_id == tenant_id)
                                .order_by(IncidentNoticeDelivery.sent_at.desc()).limit(limit)).all())


def issued(session: Session, limit: int = 100) -> list[IncidentNotice]:
    """The installation's notices, newest first, for the operator."""
    return list(session.execute(select(IncidentNotice).order_by(IncidentNotice.issued_at.desc()).limit(limit)).scalars())
