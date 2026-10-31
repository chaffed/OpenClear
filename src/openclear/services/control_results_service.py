# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Control test results and deviations (CONTROLS_PLAN.md CT2; domain/control_result.py).

- **Results.** Every scheduled run of a check is kept (`started_by="schedule"`); a page view
  keeps a result only when the check's status changed from its last result (CT-D10), so viewing
  doesn't flood the table. A population check's result carries the period it covers, and is
  recorded nightly (or on demand for any past period), never from a view.
- **Deviations.** A result at FAIL (or at WARN, for the checks in the bank's
  `control_warn_deviations`) opens a deviation when none is open for that check; one that no
  longer counts closes it. Each end links its result. Someone with `compliance:manage` records
  the cause and the response (respond).
- **Retention.** Housekeeping removes results and closed deviations older than
  OPENCLEAR_CONTROL_RESULT_RETENTION_DAYS (seven years by default)."""

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from openclear.errors import UserFacingError
from openclear.domain.control_result import ControlDeviation, ControlTestResult
from openclear.domain.tenant import Tenant

RESPONSE_LIMIT = 4000


class DeviationError(UserFacingError):
    """The message is fit to show."""


@dataclass
class Outcome:
    """One check's run, ready to record."""

    key: str
    status: str
    summary: str
    evidence: dict
    duration_ms: int = 0
    period_start: datetime | None = None
    period_end: datetime | None = None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _plain(evidence: dict) -> dict:
    """The evidence as JSON would keep it (dates as strings), so its digest is reproducible."""
    return json.loads(json.dumps(evidence or {}, default=str))


def digest(evidence: dict) -> str:
    return hashlib.sha256(json.dumps(_plain(evidence), sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def latest(session: Session, tenant_id: uuid.UUID, check_key: str) -> ControlTestResult | None:
    return session.execute(select(ControlTestResult).where(ControlTestResult.tenant_id == tenant_id, ControlTestResult.check_key == check_key)
                           .order_by(ControlTestResult.recorded_at.desc(), ControlTestResult.id.desc()).limit(1)).scalar_one_or_none()


def latest_by_key(session: Session, tenant_id: uuid.UUID, keys) -> dict[str, ControlTestResult]:
    return {k: r for k in keys if (r := latest(session, tenant_id, k)) is not None}


def _counts(tenant: Tenant, key: str, status: str) -> bool:
    """Whether this status is a deviation for this check."""
    return status == "FAIL" or (status == "WARN" and key in (tenant.control_warn_deviations or []))


def record(session: Session, tenant_id: uuid.UUID, outcomes: list[Outcome], *, started_by: str, only_changes: bool = False,
           user_id: uuid.UUID | None = None) -> list[ControlTestResult]:
    """Keep these results (all of them, or only those whose status changed from the last), and
    open, worsen or close each check's deviation. Flushes; the caller commits."""
    tenant = session.get(Tenant, tenant_id)
    kept = []
    for outcome in outcomes:
        last = latest(session, tenant_id, outcome.key)
        if only_changes and last is not None and last.status == outcome.status:
            continue
        evidence = _plain(outcome.evidence)
        result = ControlTestResult(
            tenant_id=tenant_id, check_key=outcome.key, status=outcome.status, summary=outcome.summary[:4000], evidence=evidence,
            evidence_sha256=digest(evidence), period_start=outcome.period_start, period_end=outcome.period_end, started_by=started_by,
            user_id=user_id, duration_ms=outcome.duration_ms, recorded_at=_now(),
        )
        session.add(result)
        session.flush()
        kept.append(result)
        _deviation_for(session, tenant, result)
    return kept


def _deviation_for(session: Session, tenant: Tenant, result: ControlTestResult) -> None:
    open_ = session.execute(select(ControlDeviation).where(ControlDeviation.tenant_id == tenant.id, ControlDeviation.check_key == result.check_key,
                                                           ControlDeviation.closed_at.is_(None))).scalar_one_or_none()
    counts = _counts(tenant, result.check_key, result.status)
    if counts and open_ is None:
        session.add(ControlDeviation(tenant_id=tenant.id, check_key=result.check_key, status=result.status, opened_at=result.recorded_at,
                                     opened_result_id=result.id))
    elif counts and open_ is not None and result.status == "FAIL" and open_.status != "FAIL":
        open_.status = "FAIL"  # it got worse while open
    elif not counts and open_ is not None:
        open_.closed_at, open_.closed_result_id = result.recorded_at, result.id
    session.flush()


def outcome_from(control, *, duration_ms: int = 0, period: tuple[datetime, datetime] | None = None) -> Outcome:
    return Outcome(key=control.key, status=control.status, summary=control.detail, evidence=control.evidence, duration_ms=duration_ms,
                   period_start=period[0] if period else None, period_end=period[1] if period else None)


# --- Running checks on schedule and on demand --------------------------------------------------


def run_checks(session: Session, tenant_id: uuid.UUID, checks, *, period: tuple[datetime, datetime] | None = None) -> list:
    """Run these checks now (a population check over `period`), each timed. Returns (Control,
    Outcome) pairs. A check that raises is recorded as FAIL with the error, so a broken check
    shows rather than vanishing."""
    import time

    from openclear.services.controls_service import FAIL, Control

    out = []
    for check in checks:
        started = time.monotonic()
        try:
            if check.kind == "population":
                start, end = period or (_now() - timedelta(days=1), _now())
                control = check.run_now(session, tenant_id, start=start, end=end)
            else:
                control = check.run_now(session, tenant_id)
        except Exception as exc:  # noqa: BLE001 -- one check's failure is its own result
            session.rollback()
            control = Control(check.key, check.title, FAIL, f"The check couldn't run: {type(exc).__name__}: {exc}", {"error": str(exc)[:500]})
        duration = int((time.monotonic() - started) * 1000)
        out.append((control, outcome_from(control, duration_ms=duration,
                                          period=(period or (_now() - timedelta(days=1), _now())) if check.kind == "population" else None)))
    return out


def previous_day(now: datetime | None = None) -> tuple[datetime, datetime]:
    """The previous UTC day: what the nightly population run covers."""
    today = (now or _now()).astimezone(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    return today - timedelta(days=1), today


def test_period(session: Session, tenant_id: uuid.UUID, start: datetime, end: datetime, *, user_id: uuid.UUID | None = None) -> list:
    """Every population check over a past period, on demand (CT-D11), recorded as a person's run."""
    from openclear.compliance import checks

    population = [c for c in checks.registry() if c.kind == "population"]
    ran = run_checks(session, tenant_id, population, period=(start, end))
    return record(session, tenant_id, [o for _c, o in ran], started_by="person", user_id=user_id)


# --- Deviations --------------------------------------------------------------------------------


def deviations(session: Session, tenant_id: uuid.UUID, *, check_key: str | None = None, limit: int = 200) -> list[ControlDeviation]:
    stmt = select(ControlDeviation).where(ControlDeviation.tenant_id == tenant_id)
    if check_key:
        stmt = stmt.where(ControlDeviation.check_key == check_key)
    return list(session.execute(stmt.order_by(ControlDeviation.closed_at.is_not(None), ControlDeviation.opened_at.desc()).limit(limit)).scalars())


def get_deviation(session: Session, tenant_id: uuid.UUID, deviation_id: uuid.UUID) -> ControlDeviation | None:
    found = session.get(ControlDeviation, deviation_id)
    return found if found is not None and found.tenant_id == tenant_id else None


def respond(session: Session, tenant_id: uuid.UUID, deviation_id: uuid.UUID, *, cause: str, response: str,
            user_id: uuid.UUID) -> ControlDeviation:
    deviation = get_deviation(session, tenant_id, deviation_id)
    if deviation is None:
        raise DeviationError("Deviation not found.")
    cause, response = (cause or "").strip(), (response or "").strip()
    if not cause or not response:
        raise DeviationError("Say what caused it and what you did about it.")
    if len(cause) > RESPONSE_LIMIT or len(response) > RESPONSE_LIMIT:
        raise DeviationError(f"Keep each to {RESPONSE_LIMIT} characters.")
    deviation.cause, deviation.response, deviation.responded_by_user_id, deviation.responded_at = cause, response, user_id, _now()
    session.flush()
    return deviation


def history(session: Session, tenant_id: uuid.UUID, check_key: str, *, days: int = 90) -> list[ControlTestResult]:
    since = _now() - timedelta(days=days)
    return list(session.execute(select(ControlTestResult).where(
        ControlTestResult.tenant_id == tenant_id, ControlTestResult.check_key == check_key, ControlTestResult.recorded_at >= since)
        .order_by(ControlTestResult.recorded_at.desc()).limit(500)).scalars())


def daily_strip(results: list[ControlTestResult], *, days: int = 30, now: datetime | None = None) -> list[tuple[str, str | None]]:
    """The worst status recorded each day, oldest first, for the last `days` days: (date, status
    or None when nothing was recorded that day)."""
    order = {"PASS": 0, "WARN": 1, "FAIL": 2}
    today = (now or _now()).date()
    worst: dict = {}
    for r in results:
        day = (r.recorded_at if r.recorded_at.tzinfo else r.recorded_at.replace(tzinfo=timezone.utc)).date()
        if day not in worst or order[r.status] > order[worst[day]]:
            worst[day] = r.status
    return [((today - timedelta(days=i)).isoformat(), worst.get(today - timedelta(days=i))) for i in range(days - 1, -1, -1)]


def prune(session: Session, *, older_than: datetime) -> tuple[int, int]:
    """Results, and closed deviations, recorded before `older_than` (housekeeping). A result a
    remaining deviation links (an open one's opening result, say) is kept with it."""
    deviations_gone = session.execute(delete(ControlDeviation).where(ControlDeviation.closed_at < older_than),
                                      execution_options={"synchronize_session": False}).rowcount or 0
    linked = set(session.execute(select(ControlDeviation.opened_result_id)).scalars())
    linked |= {i for i in session.execute(select(ControlDeviation.closed_result_id)).scalars() if i is not None}
    old = [i for i in session.execute(select(ControlTestResult.id).where(ControlTestResult.recorded_at < older_than)).scalars()
           if i not in linked]
    for chunk in range(0, len(old), 1000):
        session.execute(delete(ControlTestResult).where(ControlTestResult.id.in_(old[chunk:chunk + 1000])),
                        execution_options={"synchronize_session": False})
    return len(old), deviations_gone
