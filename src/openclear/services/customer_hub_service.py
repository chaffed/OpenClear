# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""The customer hub (UX_PLAN.md U7): each customer with what bank staff need to act on,
in a few grouped queries rather than one per customer: items open, items due today, the
nearest deadline, the last issued-check file, and the decision default at the deadline.
Needs-action first."""

import uuid
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone

from sqlalchemy import case, func, or_, select
from sqlalchemy.orm import Session

from openclear.domain.bulk_upload_file import BulkUploadFile, BulkUploadKind
from openclear.domain.customer import Customer
from openclear.domain.customer_disposition_setting import CustomerDispositionSetting, DispositionMode
from openclear.domain.exception_item import ExceptionItem
from openclear.services.exception_service import NEEDS_ATTENTION

_MODE_LABELS = {DispositionMode.FIXED_PAY: "Pay", DispositionMode.FIXED_RETURN: "Return",
                DispositionMode.ML_DETERMINED: "The model's call", DispositionMode.NONE: "Stays open"}


@dataclass
class HubRow:
    customer: Customer
    open_count: int
    due_today: int
    nearest_deadline: datetime | None
    last_issue_file_at: datetime | None
    decision_default: str
    # Its cut-off per network ("check": "11:00") and the networks agreed with it rather than the
    # bank's (UI_PLAN.md UI-D13).
    cutoffs: dict = None
    negotiated: frozenset = frozenset()

    @property
    def needs_action(self) -> bool:
        return self.open_count > 0

    def days_since_issue_file(self, now: datetime) -> int | None:
        if self.last_issue_file_at is None:
            return None
        last = self.last_issue_file_at if self.last_issue_file_at.tzinfo else self.last_issue_file_at.replace(tzinfo=timezone.utc)
        return (now - last).days


def _aware(value: datetime | None) -> datetime | None:
    return value.replace(tzinfo=timezone.utc) if value is not None and value.tzinfo is None else value


def rows(session: Session, tenant_id: uuid.UUID, *, q: str = "", quiet_days: int | None = None,
         now: datetime | None = None) -> list[HubRow]:
    """Every customer (or those matching `q`), needs-action first, then by name. With
    `quiet_days`, only those with no issued-check file in that many days (or ever)."""
    now = now or datetime.now(timezone.utc)
    end_of_today = datetime.combine(now.date(), time.max, tzinfo=timezone.utc)
    stmt = select(Customer).where(Customer.tenant_id == tenant_id)
    if q.strip():
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(Customer.name.ilike(like), Customer.customer_number.ilike(like)))
    customers = list(session.execute(stmt).scalars())
    if not customers:
        return []

    open_stats = {cid: (count, due, nearest) for cid, count, due, nearest in session.execute(
        select(ExceptionItem.customer_id, func.count(ExceptionItem.id),
               func.sum(case((ExceptionItem.decision_deadline <= end_of_today, 1), else_=0)),
               func.min(ExceptionItem.decision_deadline))
        .where(ExceptionItem.tenant_id == tenant_id, ExceptionItem.status.in_(list(NEEDS_ATTENTION)), ExceptionItem.customer_id.is_not(None))
        .group_by(ExceptionItem.customer_id)).all()}
    last_files = dict(session.execute(
        select(BulkUploadFile.customer_id, func.max(BulkUploadFile.uploaded_at))
        .where(BulkUploadFile.tenant_id == tenant_id, BulkUploadFile.kind == BulkUploadKind.ISSUED_ITEMS,
               BulkUploadFile.customer_id.is_not(None))
        .group_by(BulkUploadFile.customer_id)).all())
    modes: dict[uuid.UUID, set] = {}
    agreed: dict[uuid.UUID, dict] = {}
    for cid, mode, network, cutoff in session.execute(
            select(CustomerDispositionSetting.customer_id, CustomerDispositionSetting.mode, CustomerDispositionSetting.network_code,
                   CustomerDispositionSetting.cutoff_time).where(CustomerDispositionSetting.tenant_id == tenant_id)).all():
        modes.setdefault(cid, set()).add(mode)
        if cutoff:
            agreed.setdefault(cid, {})[network] = cutoff
    from openclear.domain.tenant import Tenant

    bank_cutoffs = dict(session.get(Tenant, tenant_id).decision_cutoffs or {})

    result = []
    for customer in customers:
        count, due, nearest = open_stats.get(customer.id, (0, 0, None))
        customer_modes = (modes.get(customer.id) or {DispositionMode.NONE}) - ({DispositionMode.NONE} if len(modes.get(customer.id) or ()) > 1 else set())
        default = _MODE_LABELS[next(iter(customer_modes))] if len(customer_modes) == 1 else "Varies by network"
        own = agreed.get(customer.id, {})
        row = HubRow(customer, count or 0, int(due or 0), _aware(nearest), _aware(last_files.get(customer.id)), default,
                     cutoffs={**bank_cutoffs, **own}, negotiated=frozenset(own))
        if quiet_days is not None:
            days = row.days_since_issue_file(now)
            if days is not None and days < quiet_days:
                continue
        result.append(row)
    far = datetime.max.replace(tzinfo=timezone.utc)
    result.sort(key=lambda r: (not r.needs_action, r.nearest_deadline or far, r.customer.name.lower()))
    return result
