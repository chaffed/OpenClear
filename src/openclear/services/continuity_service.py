# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""What the cut-off sweep does after a gap (CONTINUITY_PLAN.md BC-D1).

The disposition sweep decides every open item whose deadline has passed. When OpenClear was down
across a cut-off, that means the first sweep after it comes back applies each customer's default
to every item nobody could look at. Here, a gap longer than the sweep's interval is treated as an
outage: every open item whose deadline fell inside it gets the bank's minimum review time again,
never past a check's midnight deadline, and the bank's administrators and the customers' deciders
are told. Items whose midnight deadline already passed stay due at once, flagged as RG1 flags them."""

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.orm import Session

from openclear.config import get_settings
from openclear.db.tenancy import bind_session_to_tenant
from openclear.domain.exception_item import ExceptionItem, ExceptionStatus
from openclear.domain.scheduled_job_run import ScheduledJobRun
from openclear.domain.tenant import Tenant

logger = logging.getLogger(__name__)

SWEEP_JOB_ID = "sweep_expired_dispositions_job"
PAUSED_MARKER = SWEEP_JOB_ID + ":paused"  # a ScheduledJobRun row: when maintenance mode first stopped the sweep (BC-D4)
GRACE = timedelta(minutes=5)  # a slow sweep isn't an outage
OUTAGE = "outage"  # exception_item.deadline_moved_reason


@dataclass
class Gap:
    tenant_id: uuid.UUID
    unavailable_from: datetime
    unavailable_to: datetime
    moved: int = 0
    due_again_at: datetime | None = None  # the earliest new deadline
    by_customer: dict[uuid.UUID | None, int] = field(default_factory=dict)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime | None) -> datetime | None:
    return value.replace(tzinfo=timezone.utc) if value is not None and value.tzinfo is None else value


def note_paused(session: Session, now: datetime | None = None) -> None:
    """Maintenance mode stopped the sweep: remember when it began, so the pause counts as a gap
    once it ends (the sweep's own run row keeps being written by the scheduler meanwhile)."""
    now = now or _now()
    marker = session.get(ScheduledJobRun, PAUSED_MARKER)
    if marker is None:
        session.add(ScheduledJobRun(job_id=PAUSED_MARKER, last_started_at=now, last_finished_at=None, last_ok=None))
    session.flush()


def gap_before(session: Session, now: datetime) -> tuple[datetime, datetime] | None:
    """(from, to) when the sweep's previous run finished longer ago than its interval allows, or
    when maintenance mode paused it: from the pause's start (the marker is then cleared)."""
    marker = session.get(ScheduledJobRun, PAUSED_MARKER)
    if marker is not None:
        since = _aware(marker.last_started_at)
        session.delete(marker)
        session.flush()
        return (since, now)
    run = session.get(ScheduledJobRun, SWEEP_JOB_ID)
    last = _aware(run.last_finished_at) if run else None
    if last is None:
        return None
    interval = timedelta(seconds=get_settings().disposition_sweep_interval_seconds)
    return (last, now) if now - last > interval + GRACE else None


def recover_from_gap(session: Session, now: datetime | None = None) -> list[Gap]:
    """Before the sweep decides anything: moves the deadlines that fell in the gap, records it
    in each bank's action log (continuity.gap, a high security event) and tells people. Commits
    per bank; leaves the session unbound. Returns what was done, per bank."""
    from openclear.services import audit_log_service, notification_service

    now = now or _now()
    gap = gap_before(session, now)
    if gap is None:
        return []
    since, until = gap
    bind_session_to_tenant(session, None)
    rows = session.execute(select(ExceptionItem.id, ExceptionItem.tenant_id).where(
        ExceptionItem.status.in_([ExceptionStatus.OPEN, ExceptionStatus.PENDING_APPROVAL]),
        ExceptionItem.decision_deadline > since, ExceptionItem.decision_deadline <= until)).all()
    by_tenant: dict[uuid.UUID, list[uuid.UUID]] = {}
    for item_id, tenant_id in rows:
        by_tenant.setdefault(tenant_id, []).append(item_id)
    done: list[Gap] = []
    for tenant_id, item_ids in by_tenant.items():
        bind_session_to_tenant(session, tenant_id)
        try:
            tenant = session.get(Tenant, tenant_id)
            summary = Gap(tenant_id, since, until)
            window = timedelta(minutes=tenant.cutoff_min_review_minutes)
            for item in session.execute(select(ExceptionItem).where(ExceptionItem.id.in_(item_ids))).scalars():
                due = now + window
                returns_by = _aware(item.return_deadline)
                if returns_by is not None:
                    due = min(due, returns_by)
                if due <= now:
                    continue  # its midnight deadline has passed: due at once, and flagged as late when decided
                item.decision_deadline, item.deadline_moved_reason, item.deadline_warned_at = due, OUTAGE, None
                summary.moved += 1
                summary.due_again_at = due if summary.due_again_at is None else min(summary.due_again_at, due)
                summary.by_customer[item.customer_id] = summary.by_customer.get(item.customer_id, 0) + 1
            session.flush()
            audit_log_service.record_action(
                session, tenant_id, actor_user_id=None, channel="system", action="continuity.gap",
                summary=(f"OpenClear was unavailable from {since:%Y-%m-%d %H:%M} to {until:%H:%M} UTC, across a cut-off; "
                         f"{summary.moved} items due in that time are due again from {summary.due_again_at:%H:%M} UTC"
                         if summary.moved else
                         f"OpenClear was unavailable from {since:%Y-%m-%d %H:%M} to {until:%H:%M} UTC; {len(item_ids)} items were due in "
                         "that time and are past their midnight deadline, so they are due at once")[:500],
                resource_type="tenant", resource_id=tenant_id,
                metadata={"unavailable_from": since.isoformat(), "unavailable_to": until.isoformat(), "items_due_in_gap": len(item_ids),
                          "moved": summary.moved, "due_again_at": summary.due_again_at.isoformat() if summary.due_again_at else None})
            if summary.moved:
                notification_service.notify_continuity_gap(session, tenant, summary, zone=_zone(tenant))
            session.commit()
            done.append(summary)
            logger.warning("Continuity gap for bank %s: %d of %d items due again from %s", tenant_id, summary.moved, len(item_ids),
                           summary.due_again_at)
        except Exception:  # noqa: BLE001 -- one bank's failure doesn't stop the others, nor the sweep
            session.rollback()
            logger.exception("Recovering from the gap failed for bank %s", tenant_id)
    bind_session_to_tenant(session, None)
    return done


def _zone(tenant: Tenant) -> ZoneInfo:
    from openclear.services.cutoff_service import time_zone_for

    try:
        return ZoneInfo(time_zone_for(tenant))
    except ZoneInfoNotFoundError:
        return ZoneInfo("UTC")
