# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed
"""Decision cut-offs (UI_PLAN.md UI-D13): when each item must be decided by.

The bank sets a time of day per network, in its own time zone, and a minimum review time; a
customer may have its own time, agreed with the bank (CustomerDispositionSetting.cutoff_time).
An item's deadline is the first cut-off, on a business day, at least the minimum review time
after it arrives. Business days are Monday to Friday, less the Federal Reserve's holidays and
the bank's own closures.

A network with no cut-off set has no deadline from here: the caller falls back to the customer's
response window, as before cut-offs existed."""

import re
import uuid
from datetime import date, datetime, time, timedelta, timezone
from functools import lru_cache
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones

from sqlalchemy import select
from sqlalchemy.orm import Session

from openclear.domain.bank_closure import BankClosure
from openclear.domain.customer_disposition_setting import CustomerDispositionSetting
from openclear.domain.tenant import Tenant

_TIME = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
MAX_REVIEW_MINUTES = 24 * 60


class CutoffError(ValueError):
    """The message is fit to show."""


# --- The Federal Reserve's holidays -------------------------------------------------------------


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    first = date(year, month, 1)
    return first + timedelta(days=(weekday - first.weekday()) % 7 + 7 * (n - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    last = (date(year, month + 1, 1) if month < 12 else date(year + 1, 1, 1)) - timedelta(days=1)
    return last - timedelta(days=(last.weekday() - weekday) % 7)


@lru_cache(maxsize=64)
def federal_reserve_holidays(year: int) -> frozenset[date]:
    """The days the Federal Reserve Banks are closed in `year`. A holiday on a Sunday is kept
    on the Monday after; one on a Saturday isn't moved (the Reserve Banks open the Friday)."""
    mon, thu = 0, 3
    fixed = [date(year, 1, 1), date(year, 6, 19), date(year, 7, 4), date(year, 11, 11), date(year, 12, 25)]
    moving = [
        _nth_weekday(year, 1, mon, 3),   # Birthday of Martin Luther King, Jr.
        _nth_weekday(year, 2, mon, 3),   # Washington's Birthday
        _last_weekday(year, 5, mon),     # Memorial Day
        _nth_weekday(year, 9, mon, 1),   # Labor Day
        _nth_weekday(year, 10, mon, 2),  # Columbus Day
        _nth_weekday(year, 11, thu, 4),  # Thanksgiving Day
    ]
    observed = {day + timedelta(days=1) if day.weekday() == 6 else day for day in fixed}
    return frozenset(observed | set(moving))


def is_business_day(day: date, closures: frozenset[date] = frozenset()) -> bool:
    return day.weekday() < 5 and day not in federal_reserve_holidays(day.year) and day not in closures


# --- Settings -----------------------------------------------------------------------------------


def parse_time(value: str | None) -> str | None:
    """'HH:MM' (24-hour), or None for blank."""
    value = (value or "").strip()
    if not value:
        return None
    if not _TIME.match(value):
        raise CutoffError(f"{value!r} isn't a time of day; use 24-hour HH:MM, such as 11:00.")
    return value


def parse_time_zone(value: str | None) -> str:
    value = (value or "").strip()
    if value not in available_time_zones():
        raise CutoffError("Choose the bank's time zone from the list.")
    return value


@lru_cache(maxsize=1)
def available_time_zones() -> frozenset[str]:
    return frozenset(available_timezones())


def us_time_zones() -> list[str]:
    """The time zones offered first: the United States', east to west."""
    return ["America/New_York", "America/Chicago", "America/Denver", "America/Phoenix", "America/Los_Angeles",
            "America/Anchorage", "Pacific/Honolulu", "America/Puerto_Rico", "Pacific/Guam"]


def closures(session: Session, tenant_id: uuid.UUID) -> list[BankClosure]:
    return list(session.execute(select(BankClosure).where(BankClosure.tenant_id == tenant_id)
                                .order_by(BankClosure.closed_on)).scalars())


def customer_cutoffs(session: Session, tenant_id: uuid.UUID, customer_id: uuid.UUID) -> dict[str, str]:
    """{network: "HH:MM"} for the networks this customer has its own cut-off on."""
    rows = session.execute(select(CustomerDispositionSetting).where(
        CustomerDispositionSetting.tenant_id == tenant_id, CustomerDispositionSetting.customer_id == customer_id,
        CustomerDispositionSetting.cutoff_time.is_not(None))).scalars()
    return {row.network_code: row.cutoff_time for row in rows}


def cutoff_for(session: Session, tenant: Tenant, customer_id: uuid.UUID | None, network_code: str) -> str | None:
    """The cut-off time that applies: the customer's own, else the bank's, else none."""
    if customer_id is not None:
        own = customer_cutoffs(session, tenant.id, customer_id).get(network_code)
        if own:
            return own
    return (tenant.decision_cutoffs or {}).get(network_code)


# --- Deadlines ----------------------------------------------------------------------------------


def next_cutoff(arrived: datetime, cutoff: str, zone: str, min_review_minutes: int, closed: frozenset[date] = frozenset()) -> datetime:
    """The first `cutoff` (HH:MM in `zone`) on a business day at least `min_review_minutes`
    after `arrived`, in UTC. The local time is kept across a daylight-saving change."""
    tz = ZoneInfo(zone)
    earliest = arrived.astimezone(tz) + timedelta(minutes=min_review_minutes)
    hours, minutes = (int(part) for part in cutoff.split(":"))
    day = earliest.date()
    for _ in range(400):  # a year and more of closed days would be a configuration error
        candidate = datetime.combine(day, time(hours, minutes), tzinfo=tz)
        if candidate >= earliest and is_business_day(day, closed):
            return candidate.astimezone(timezone.utc)
        day += timedelta(days=1)
    raise CutoffError("No business day was found for the cut-off within a year; check the bank's closures.")


def deadline_for(session: Session, tenant_id: uuid.UUID, customer_id: uuid.UUID | None, network_code: str,
                 arrived: datetime | None = None) -> datetime | None:
    """An item's deadline from the cut-off, or None when this network has no cut-off (or the
    bank has no time zone yet)."""
    tenant = session.get(Tenant, tenant_id)
    if tenant is None or not tenant.time_zone:
        return None
    cutoff = cutoff_for(session, tenant, customer_id, network_code)
    if not cutoff:
        return None
    try:
        zone = ZoneInfo(tenant.time_zone)
    except ZoneInfoNotFoundError:
        return None
    closed = frozenset(c.closed_on for c in closures(session, tenant_id))
    return next_cutoff(arrived or datetime.now(timezone.utc), cutoff, zone.key, tenant.cutoff_min_review_minutes, closed)


def describe(tenant: Tenant) -> str:
    """The control-panel tile's state: "Checks 11:00 · ACH 15:00 ET" or "Not set"."""
    cutoffs = tenant.decision_cutoffs or {}
    if not tenant.time_zone or not cutoffs:
        return "Not set"
    names = {"check": "Checks", "ach": "ACH"}
    parts = [f"{names.get(network, network)} {value}" for network, value in sorted(cutoffs.items())]
    return " · ".join(parts) + f" ({tenant.time_zone.split('/')[-1].replace('_', ' ')})"


def apply_bank_cutoffs(session: Session, tenant_id: uuid.UUID, *, time_zone: str, cutoffs: dict[str, str | None],
                       min_review_minutes: int) -> Tenant:
    """Sets the bank's cut-offs. Called by the protected change (services/change_kinds.py)."""
    tenant = session.get(Tenant, tenant_id)
    tenant.time_zone = parse_time_zone(time_zone)
    tenant.decision_cutoffs = {network: value for network, value in
                               ((n, parse_time(v)) for n, v in cutoffs.items()) if value}
    if not 0 <= min_review_minutes <= MAX_REVIEW_MINUTES:
        raise CutoffError("The minimum review time is 0 to 1,440 minutes (a day).")
    tenant.cutoff_min_review_minutes = min_review_minutes
    session.flush()
    return tenant


def apply_customer_cutoffs(session: Session, tenant_id: uuid.UUID, customer_id: uuid.UUID, cutoffs: dict[str, str | None]) -> None:
    """Sets (or clears, with None) a customer's own cut-off per network."""
    for network_code, value in cutoffs.items():
        value = parse_time(value)
        row = session.execute(select(CustomerDispositionSetting).where(
            CustomerDispositionSetting.tenant_id == tenant_id, CustomerDispositionSetting.customer_id == customer_id,
            CustomerDispositionSetting.network_code == network_code)).scalar_one_or_none()
        if row is None:
            if value is None:
                continue
            row = CustomerDispositionSetting(tenant_id=tenant_id, customer_id=customer_id, network_code=network_code)
            session.add(row)
        row.cutoff_time = value
    session.flush()


def add_closure(session: Session, tenant_id: uuid.UUID, closed_on: date, name: str) -> BankClosure:
    name = (name or "").strip()
    if not name:
        raise CutoffError('Name the closure, such as "Founder\'s Day".')
    if session.execute(select(BankClosure).where(BankClosure.tenant_id == tenant_id, BankClosure.closed_on == closed_on)).first():
        raise CutoffError(f"{closed_on.isoformat()} is already a closure.")
    closure = BankClosure(tenant_id=tenant_id, closed_on=closed_on, name=name[:120])
    session.add(closure)
    session.flush()
    return closure


def remove_closure(session: Session, tenant_id: uuid.UUID, closure_id: uuid.UUID) -> BankClosure | None:
    closure = session.get(BankClosure, closure_id)
    if closure is None or closure.tenant_id != tenant_id:
        return None
    session.delete(closure)
    session.flush()
    return closure
