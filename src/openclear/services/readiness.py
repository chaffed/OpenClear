# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Whether this instance can serve (CONTINUITY_PLAN.md BC-D6): the database, file storage,
Redis when it's configured, and whether the scheduler has run lately. `/health` keeps checking
the database alone, for load balancers; `/ready` and the `openclear_component_up` metric say
which part is failing, so a partial failure (files can't be written; the sweep hasn't run for an
hour) is seen before a bank notices."""

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

PROBE_AREA = "data_exports"  # every installation has it; the probe is one byte, written and deleted
REQUIRED = ("database", "storage")  # without these the instance can't serve; the rest degrade


@dataclass(frozen=True)
class Component:
    name: str
    ok: bool
    detail: str


def database(session: Session) -> Component:
    try:
        session.execute(text("SELECT 1"))
        return Component("database", True, "answers")
    except Exception as exc:  # noqa: BLE001 -- the answer is no
        logger.exception("Readiness: the database didn't answer")
        return Component("database", False, f"unreachable ({type(exc).__name__})")


def storage() -> Component:
    from openclear import storage as files

    key = f"_ready/{uuid.uuid4().hex}"
    try:
        ref = files.put(PROBE_AREA, key, b"ready")
        files.delete(ref)
        return Component("storage", True, f"{files.backend().__class__.__name__} writes")
    except Exception as exc:  # noqa: BLE001 -- the answer is no
        logger.exception("Readiness: storage can't be written")
        return Component("storage", False, f"can't write ({type(exc).__name__})")


def redis() -> Component | None:
    """None when limits are kept in the process: nothing to check."""
    from openclear.web.rate_limit import backend_status

    backend, reachable = backend_status()
    if backend != "redis":
        return None
    return Component("redis", reachable, "answers" if reachable else "unreachable: limits held per instance")


def scheduler(session: Session, now: datetime | None = None) -> Component:
    """The cut-off sweep's last run against its interval (the same measure continuity_service
    uses for a gap). Not yet run is fine on a fresh installation."""
    from openclear.services.continuity_service import GRACE, SWEEP_JOB_ID, _aware
    from openclear.config import get_settings
    from openclear.domain.scheduled_job_run import ScheduledJobRun

    now = now or datetime.now(timezone.utc)
    run = session.get(ScheduledJobRun, SWEEP_JOB_ID)
    last = _aware(run.last_finished_at) if run else None
    if last is None:
        return Component("scheduler", True, "no run recorded yet")
    interval = timedelta(seconds=get_settings().disposition_sweep_interval_seconds)
    age = now - last
    if age > interval + GRACE:
        return Component("scheduler", False, f"the cut-off sweep last ran {int(age.total_seconds() // 60)} minutes ago")
    return Component("scheduler", True, f"the cut-off sweep ran {int(age.total_seconds() // 60)} minutes ago")


def components(session: Session) -> list[Component]:
    found = [database(session), storage()]
    cache = redis()
    if cache is not None:
        found.append(cache)
    found.append(scheduler(session) if found[0].ok else Component("scheduler", False, "unknown: the database didn't answer"))
    return found


def status(found: list[Component]) -> str:
    """ready, degraded (something optional is down) or unavailable (something required is)."""
    if any(not c.ok for c in found if c.name in REQUIRED):
        return "unavailable"
    return "degraded" if any(not c.ok for c in found) else "ready"
