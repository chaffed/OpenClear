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

from openclear.errors import UserFacingError
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


class ReminderError(UserFacingError):
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
    rows = session.execute(select(ExceptionItem.id, ExceptionItem.tenant_id, ExceptionItem.customer_id, ExceptionItem.source_item_id,
                                  ExceptionItem.decision_deadline).where(
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

    accounts = _accounts(session, [i.source_item_id for i in items])
    due_by_customer: dict[tuple, list[datetime]] = defaultdict(list)
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
        due_by_customer[(item.customer_id, accounts.get(item.source_item_id))].append(deadline)
    if not due_by_customer:
        return 0
    return _notify(session, tenant, due_by_customer, now, notification_service)


def _notify(session: Session, tenant: Tenant, due_by_customer: dict, now: datetime, notification_service) -> int:
    """One message per person: the items due that they can see, and the soonest deadline
    (None for items with no deadline). `due_by_customer` is keyed by (customer, account), so
    a person who sees only some accounts counts only theirs. Returns how many people."""
    tenant_id = tenant.id
    per_person: dict[uuid.UUID, tuple] = {}
    for (customer_id, account_id), deadlines in due_by_customer.items():
        recipients = {}
        for permission in ("exception:recommend", "exception:decide"):
            for user in notification_service._recipients_for_permission(session, tenant_id, permission, customer_id=customer_id,
                                                                        account_id=account_id):
                recipients[user.id] = user
        dated = [d for d in deadlines if d is not None]
        for user in recipients.values():
            _user, count, nearest = per_person.get(user.id, (user, 0, None))
            soonest = min(dated) if dated else None
            if nearest is not None and soonest is not None:
                soonest = min(nearest, soonest)
            per_person[user.id] = (user, count + len(deadlines), soonest or nearest)
    for user, count, nearest in per_person.values():
        due_in = duration((nearest - now).total_seconds() / 60) if nearest is not None else None
        plural = "s" if count != 1 else ""
        notification_service._queue(
            session, tenant_id=tenant_id, recipient=user, notification_type=NotificationType.DEADLINE_REMINDER,
            template_context={"count": count, "due_in": due_in, "bank_name": tenant.name,
                              "due_at": notification_service.bank_time(session, tenant_id, nearest),
                              "queue_url": notification_service._absolute_url("/ui/exceptions")},
            subject=f"{count} item{plural} to decide, due in {due_in}" if due_in else f"{count} item{plural} to decide",
        )
    session.flush()
    return len(per_person)


def remind_customer_now(session: Session, tenant_id: uuid.UUID, customer_id: uuid.UUID, *, now: datetime | None = None) -> tuple[int, int]:
    """Bank staff's "Send a reminder now" (the customer hub, UX_PLAN.md U7): one message to
    each person who can act on this customer's open items, whatever the timing. Not logged
    against the scheduled reminders, so those still go. Returns (items, people). The caller
    commits."""
    from openclear.services import notification_service

    now = now or datetime.now(timezone.utc)
    tenant = session.get(Tenant, tenant_id)
    rows = session.execute(select(ExceptionItem.source_item_id, ExceptionItem.decision_deadline).where(
        ExceptionItem.tenant_id == tenant_id, ExceptionItem.customer_id == customer_id,
        ExceptionItem.status.in_(list(NEEDS_ATTENTION)))).all()
    if not rows:
        return 0, 0
    accounts = _accounts(session, [r.source_item_id for r in rows])
    due: dict[tuple, list] = defaultdict(list)
    for row in rows:
        due[(customer_id, accounts.get(row.source_item_id))].append(_aware(row.decision_deadline) if row.decision_deadline else None)
    return len(rows), _notify(session, tenant, due, now, notification_service)


def _accounts(session: Session, source_item_ids: list[uuid.UUID]) -> dict[uuid.UUID, uuid.UUID]:
    """The account each exception's paid item or ACH transaction is on."""
    from openclear.domain.ach_transaction import AchTransaction
    from openclear.domain.paid_item import PaidItem

    found: dict[uuid.UUID, uuid.UUID] = {}
    for model in (PaidItem, AchTransaction):
        for start in range(0, len(source_item_ids), 1000):
            found.update(session.execute(select(model.id, model.account_id).where(model.id.in_(source_item_ids[start:start + 1000]))).tuples().all())
    return found

