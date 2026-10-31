# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Numbers for the service provider's monitoring (FULL_REVIEW_PLAN.md FR-D18, O2), in the
Prometheus text format at `GET /api/v1/platform/metrics` (a platform API key with the
`operations` scope). What an operator should be paged on:

- jobs queued and failed, by kind (a failed import or export nobody retried);
- decisions past their cut-off and not yet sent (the lock job not keeping up);
- decision files written and not acknowledged, and the oldest one's age (a core not taking them);
- webhook deliveries by status (failed ones exhausted every retry);
- requests served by this process, by method and status class, and their total time.

Request counts are this process's since it started; the rest is read from the database, so
every instance reports the same values for them."""

import threading
from collections import defaultdict
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

_lock = threading.Lock()
_requests: dict[tuple[str, str], int] = defaultdict(int)
_seconds: dict[tuple[str, str], float] = defaultdict(float)


def observe(method: str, status: int, seconds: float) -> None:
    """One request served (web/request_context middleware in main.py)."""
    key = (method, f"{status // 100}xx")
    with _lock:
        _requests[key] += 1
        _seconds[key] += seconds


def _line(name: str, value, **labels) -> str:
    text = ",".join(f'{k}="{str(v).replace(chr(92), chr(92) * 2).replace(chr(34), chr(92) + chr(34))}"' for k, v in labels.items())
    return f"{name}{{{text}}} {value}" if text else f"{name} {value}"


def render(session: Session) -> str:
    from openclear.db.tenancy import bind_session_to_tenant
    from openclear.domain.decision import Decision
    from openclear.domain.decision_outbox import DecisionBatch
    from openclear.domain.exception_item import ExceptionItem
    from openclear.domain.job import Job, JobStatus
    from openclear.domain.tenant import Tenant
    from openclear.domain.webhook import WebhookDelivery

    now = datetime.now(timezone.utc)
    out = ["# HELP openclear_jobs Background jobs by kind and status (queued, running, failed).", "# TYPE openclear_jobs gauge"]
    for kind, status, count in session.execute(select(Job.kind, Job.status, func.count()).where(
            Job.status.in_([JobStatus.QUEUED, JobStatus.RUNNING, JobStatus.FAILED])).group_by(Job.kind, Job.status)):
        out.append(_line("openclear_jobs", count, kind=kind, status=getattr(status, "value", status)))

    overdue = session.execute(select(func.count()).select_from(Decision).join(ExceptionItem, ExceptionItem.id == Decision.exception_item_id)
                              .where(Decision.locked_at.is_(None), ExceptionItem.decision_deadline <= now)).scalar_one()
    out += ["# HELP openclear_decisions_unsent_past_cutoff Decisions whose cut-off has passed that haven't gone out yet.",
            "# TYPE openclear_decisions_unsent_past_cutoff gauge", _line("openclear_decisions_unsent_past_cutoff", overdue)]

    # Tenant-scoped tables (row-level security): bank by bank.
    unacked, oldest = 0, None
    deliveries: dict[str, int] = defaultdict(int)
    for tenant_id in session.execute(select(Tenant.id).where(Tenant.is_active.is_(True))).scalars().all():
        bind_session_to_tenant(session, tenant_id)
        count, first = session.execute(select(func.count(), func.min(DecisionBatch.created_at)).where(
            DecisionBatch.tenant_id == tenant_id, DecisionBatch.acknowledged_at.is_(None))).one()
        unacked += count or 0
        if first is not None:
            first = first if first.tzinfo else first.replace(tzinfo=timezone.utc)
            oldest = first if oldest is None else min(oldest, first)
        for status, n in session.execute(select(WebhookDelivery.status, func.count()).where(
                WebhookDelivery.tenant_id == tenant_id).group_by(WebhookDelivery.status)):
            deliveries[getattr(status, "value", status)] += n
        session.rollback()
    out += ["# HELP openclear_decision_files_unacknowledged Decision files written that a core hasn't acknowledged.",
            "# TYPE openclear_decision_files_unacknowledged gauge", _line("openclear_decision_files_unacknowledged", unacked),
            "# HELP openclear_decision_files_oldest_unacknowledged_seconds Age of the oldest unacknowledged decision file.",
            "# TYPE openclear_decision_files_oldest_unacknowledged_seconds gauge",
            _line("openclear_decision_files_oldest_unacknowledged_seconds", int((now - oldest).total_seconds()) if oldest else 0),
            "# HELP openclear_webhook_deliveries Webhook deliveries kept, by status.", "# TYPE openclear_webhook_deliveries gauge"]
    out += [_line("openclear_webhook_deliveries", n, status=status) for status, n in sorted(deliveries.items())]

    with _lock:
        requests, seconds = dict(_requests), dict(_seconds)
    out += ["# HELP openclear_http_requests_total Requests this process served, by method and status class.",
            "# TYPE openclear_http_requests_total counter"]
    out += [_line("openclear_http_requests_total", n, method=m, status=s) for (m, s), n in sorted(requests.items())]
    out += ["# HELP openclear_http_request_seconds_total Time this process spent serving them.",
            "# TYPE openclear_http_request_seconds_total counter"]
    out += [_line("openclear_http_request_seconds_total", round(t, 3), method=m, status=s) for (m, s), t in sorted(seconds.items())]
    # Each component this instance needs, 1 or 0 (CONTINUITY_PLAN.md BC-D6; services/readiness.py).
    from openclear.services import readiness

    bind_session_to_tenant(session, None)
    out += ["# HELP openclear_component_up Whether the database, file storage, Redis (when configured) and the cut-off sweep are working.",
            "# TYPE openclear_component_up gauge"]
    for component in readiness.components(session):
        out.append(_line("openclear_component_up", 1 if component.ok else 0, component=component.name))

    return "\n".join(out) + "\n"
