# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Statuses that change with the calendar (FULL_REVIEW_PLAN.md FR-D10), rolled nightly by
housekeeping, bank by bank (under row-level security):

- a stop payment past its expiration date becomes **expired** (and its check, if the stop made
  it "stopped", is outstanding again);
- an outstanding issued check older than the bank's stale-date threshold becomes **stale**.

Matching never depended on these (it reads the dates), so this changes what people see, not what
is paid; each row's updated_at moves, so a bank's data pipeline sees the change."""

import logging
from datetime import date, timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from openclear.db.tenancy import bind_session_to_tenant
from openclear.domain.issued_item import IssuedItem, IssuedItemStatus
from openclear.domain.stop_payment import StopPayment, StopPaymentStatus
from openclear.domain.tenant import Tenant

logger = logging.getLogger(__name__)


def roll(session: Session, today: date | None = None) -> dict[str, int]:
    """Rolls every active bank's statuses. Commits per bank. Returns counts."""
    from openclear.services.stop_payment_service import sync_issued_status

    today = today or date.today()
    counts = {"stops_expired": 0, "checks_stale": 0}
    for tenant_id, threshold in session.execute(select(Tenant.id, Tenant.stale_date_threshold_days).where(Tenant.is_active.is_(True))).all():
        bind_session_to_tenant(session, tenant_id)
        try:
            expired = session.execute(select(StopPayment).where(
                StopPayment.tenant_id == tenant_id, StopPayment.status == StopPaymentStatus.ACTIVE,
                StopPayment.expiration_date.is_not(None), StopPayment.expiration_date < today)).scalars().all()
            for stop in expired:
                stop.status = StopPaymentStatus.EXPIRED
            session.flush()
            for stop in expired:
                sync_issued_status(session, tenant_id, stop.account_id, stop.check_number)
            stale = session.execute(update(IssuedItem).where(
                IssuedItem.tenant_id == tenant_id, IssuedItem.status == IssuedItemStatus.OUTSTANDING,
                IssuedItem.issue_date < today - timedelta(days=threshold)).values(status=IssuedItemStatus.STALE)
                .execution_options(synchronize_session=False)).rowcount
            session.commit()
            counts["stops_expired"] += len(expired)
            counts["checks_stale"] += stale
        except Exception:  # noqa: BLE001 -- one bank's failure never stops another's
            session.rollback()
            logger.exception("Rolling statuses failed for bank %s", tenant_id)
    # Housekeeping goes on unbound, across every bank, as it started.
    from openclear.db.tenancy import _RLS_TENANT_KEY, _RLS_USER_KEY

    session.info.pop(_RLS_TENANT_KEY, None)
    session.info.pop(_RLS_USER_KEY, None)
    return counts
