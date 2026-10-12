# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Reminders before the cutoff (UX_PLAN.md U3): "N items to decide, due in X", sent some
minutes before open exceptions' decision deadlines to the people who can recommend or
decide them, by email and SMS through their notification preferences.

Timing is a list of offsets in minutes ("120,30": two hours and thirty minutes before).
The installation sets a default (config.Settings.reminder_offsets_minutes), each bank its
own in Settings → Reminders, and each customer can override its bank's from its own
settings. On the bank and the customer, null follows the level above and "" turns
reminders off.

Grouped: one message per person per sweep, covering every item due that they can see, not
one per item. Each item is reminded once per offset (deadline_reminder). Run by the
disposition sweep (workers/tasks.sweep_expired_dispositions_job)."""

import logging
import uuid
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from openclear.config import get_settings
from openclear.db.tenancy import bind_session_to_tenant
from openclear.domain.customer import Customer
from openclear.domain.deadline_reminder import DeadlineReminder
from openclear.domain.exception_item import ExceptionItem
from openclear.domain.notification import NotificationType
from openclear.domain.tenant import Tenant
from openclear.services.exception_service import NEEDS_ATTENTION

logger = logging.getLogger(__name__)

MAX_OFFSET_MINUTES = 24 * 60
MAX_OFFSETS = 4


class ReminderError(ValueError):
    """Bad input on the settings form; the message is fit to show."""


def parse_offsets(raw: str | None) -> list[int]:
    """"120, 30" or "2h, 30m" or "2 hours, 30" into [120, 30], largest first."""
    offsets = set()
    for part in (raw or "").replace(";", ",").split(","):
        text = part.strip().lower().replace(" ", "")
        if not text:
            continue
        unit = 1
        for suffix, factor in (("hours", 60), ("hour", 60), ("hrs", 60), ("hr", 60), ("h", 60),
                               ("minutes", 1), ("minute", 1), ("mins", 1), ("min", 1), ("m", 1)):
            if text.endswith(suffix):
                text, unit = text[: -len(suffix)], factor
                break
        try:
            minutes = round(float(text) * unit)
        except ValueError:
            raise ReminderError(f"{part.strip()!r} isn't a number of minutes or hours.") from None
        if not 1 <= minutes <= MAX_OFFSET_MINUTES:
            raise ReminderError("Each reminder must be between 1 minute and 24 hours before the cutoff.")
        offsets.add(minutes)
    if len(offsets) > MAX_OFFSETS:
        raise ReminderError(f"At most {MAX_OFFSETS} reminders.")
    return sorted(offsets, reverse=True)


def format_offsets(offsets: list[int]) -> str:
    return ",".join(str(m) for m in offsets)


def duration(minutes: float) -> str:
    """90 → "1 hour 30 minutes"."""
    minutes = max(0, round(minutes))
    hours, rest = divmod(minutes, 60)
    parts = []
    if hours:
        parts.append(f"{hours} hour{'s' if hours != 1 else ''}")
    if rest or not hours:
        parts.append(f"{rest} minute{'s' if rest != 1 else ''}")
    return " ".join(parts)


def describe(offsets: list[int]) -> str:
    """[120, 30] → "2 hours and 30 minutes before the cutoff"; [] → "Off"."""
    if not offsets:
        return "Off"
    names = [duration(m) for m in offsets]
    joined = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]
    return f"{joined} before the cutoff"


def installation_offsets() -> list[int]:
    try:
        return parse_offsets(get_settings().reminder_offsets_minutes)
    except ReminderError:
        logger.warning("OPENCLEAR_REMINDER_OFFSETS_MINUTES isn't valid; reminders are off until it's fixed")
        return []


def bank_offsets(tenant: Tenant) -> list[int]:
    if tenant.reminder_offsets_minutes is None:
        return installation_offsets()
    return parse_offsets(tenant.reminder_offsets_minutes)


def customer_offsets(tenant: Tenant, customer: Customer | None) -> list[int]:
    if customer is None or customer.reminder_offsets_minutes is None:
        return bank_offsets(tenant)
    return parse_offsets(customer.reminder_offsets_minutes)


def set_bank_reminders(session: Session, tenant_id: uuid.UUID, *, mode: str, offsets: str = "") -> Tenant:
    """mode: "default" (the installation's), "off", or "custom" with `offsets`."""
    tenant = session.get(Tenant, tenant_id)
    tenant.reminder_offsets_minutes = _stored(mode, offsets, follow="default")
    session.flush()
    return tenant


def set_customer_reminders(session: Session, tenant_id: uuid.UUID, customer_id: uuid.UUID, *, mode: str, offsets: str = "") -> Customer:
    """mode: "bank" (follow the bank's), "off", or "custom" with `offsets`."""
    customer = session.get(Customer, customer_id)
    if customer is None or customer.tenant_id != tenant_id:
        raise ReminderError("Customer not found.")
    customer.reminder_offsets_minutes = _stored(mode, offsets, follow="bank")
    session.flush()
    return customer


def _stored(mode: str, offsets: str, *, follow: str) -> str | None:
    if mode == follow:
        return None
    if mode == "off":
        return ""
    if mode != "custom":
        raise ReminderError("Choose when to send reminders.")
    parsed = parse_offsets(offsets)
    if not parsed:
        raise ReminderError("Enter at least one time, such as 120, 30.")
    return format_offsets(parsed)


# --- Sending -------------------------------------------------------------------------------------


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def send_due(session: Session, now: datetime | None = None) -> int:
    """Queues the reminders now due, across every bank. Returns how many messages (one
    per person per bank) were queued. Commits per bank."""
    from openclear.services import notification_service

    now = now or datetime.now(timezone.utc)
    rows = session.execute(select(ExceptionItem.id, ExceptionItem.tenant_id, ExceptionItem.customer_id, ExceptionItem.decision_deadline).where(
        ExceptionItem.status.in_(list(NEEDS_ATTENTION)), ExceptionItem.decision_deadline.is_not(None),
        ExceptionItem.decision_deadline > now, ExceptionItem.decision_deadline <= now + timedelta(minutes=MAX_OFFSET_MINUTES),
    )).all()
    by_tenant: dict[uuid.UUID, list] = defaultdict(list)
    for row in rows:
        by_tenant[row.tenant_id].append(row)

    queued = 0
    for tenant_id, items in by_tenant.items():
        bind_session_to_tenant(session, tenant_id)
        try:
            queued += _send_for_bank(session, tenant_id, items, now, notification_service)
            session.commit()
        except Exception:  # noqa: BLE001 -- one bank's failure never stops another's reminders
            session.rollback()
            logger.exception("Reminders for bank %s failed", tenant_id)
    return queued


def _send_for_bank(session: Session, tenant_id: uuid.UUID, items: list, now: datetime, notification_service) -> int:
    tenant = session.get(Tenant, tenant_id)
    customers = {c.id: c for c in session.execute(select(Customer).where(
        Customer.id.in_({i.customer_id for i in items if i.customer_id is not None}))).scalars()}
    sent = defaultdict(set)
    for exception_item_id, offset in session.execute(select(DeadlineReminder.exception_item_id, DeadlineReminder.offset_minutes).where(
            DeadlineReminder.exception_item_id.in_([i.id for i in items]))).all():
        sent[exception_item_id].add(offset)

    due_by_customer: dict[uuid.UUID | None, list[datetime]] = defaultdict(list)
    for item in items:
        try:
            offsets = customer_offsets(tenant, customers.get(item.customer_id))
        except ReminderError:
            continue
        deadline = _aware(item.decision_deadline)
        crossed = [o for o in offsets if deadline - now <= timedelta(minutes=o) and o not in sent[item.id]]
        if not crossed:
            continue
        for offset in crossed:
            session.add(DeadlineReminder(tenant_id=tenant_id, exception_item_id=item.id, offset_minutes=offset))
        due_by_customer[item.customer_id].append(deadline)
    if not due_by_customer:
        return 0

    # One message per person: the items due that they can see, and the soonest deadline.
    per_person: dict[uuid.UUID, tuple] = {}
    for customer_id, deadlines in due_by_customer.items():
        recipients = {}
        for permission in ("exception:recommend", "exception:decide"):
            for user in notification_service._recipients_for_permission(session, tenant_id, permission, customer_id=customer_id):
                recipients[user.id] = user
        for user in recipients.values():
            _user, count, nearest = per_person.get(user.id, (user, 0, None))
            soonest = min(deadlines)
            per_person[user.id] = (user, count + len(deadlines), soonest if nearest is None else min(nearest, soonest))
    for user, count, nearest in per_person.values():
        due_in = duration((nearest - now).total_seconds() / 60)
        notification_service._queue(
            session, tenant_id=tenant_id, recipient=user, notification_type=NotificationType.DEADLINE_REMINDER,
            template_context={"count": count, "due_in": due_in, "bank_name": tenant.name,
                              "queue_url": notification_service._absolute_url("/ui/exceptions")},
            subject=f"{count} item{'s' if count != 1 else ''} to decide, due in {due_in}",
        )
    session.flush()
    return len(per_person)
