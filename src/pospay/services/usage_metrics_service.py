# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Per-tenant usage counts for the billing/account-analysis integration
(api/v1/platform_usage.py) — PosPay only ever reports counts here, it never prices or
invoices anything itself.

Two kinds of dimension, deliberately handled differently:
- Census (customers, accounts, users): always the current live count, regardless of the
  requested period — confirmed with the user as in-scope for v1; no snapshot table, no
  historical point-in-time reconstruction.
- Activity (paid items, exceptions, returns, SMS notifications, bulk uploads): counted
  within [period_start, period_end], inclusive, using each table's own timestamp column.
"""

import uuid
from dataclasses import dataclass
from datetime import date, datetime, time, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from pospay.db.tenancy import acting_as_tenant
from pospay.domain.account import Account
from pospay.domain.ach_transaction import AchTransaction
from pospay.domain.bulk_upload_file import BulkUploadFile
from pospay.domain.customer import Customer
from pospay.domain.decision import Decision, DecisionOutcome
from pospay.domain.exception_item import ExceptionItem
from pospay.domain.notification import Notification, NotificationChannel, NotificationStatus
from pospay.domain.paid_item import PaidItem
from pospay.domain.tenant import Tenant
from pospay.domain.tenant_membership import TenantMembership


@dataclass(frozen=True, slots=True)
class TenantUsage:
    tenant_id: uuid.UUID
    tenant_slug: str
    tenant_name: str
    customers: int
    accounts: int
    users: int
    paid_items_check: int
    paid_items_ach: int
    paid_items_total: int
    exceptions: int
    returns: int
    sms_notifications: int
    bulk_uploads: int


def _period_bounds(period_start: date, period_end: date) -> tuple[datetime, datetime]:
    # Inclusive on both ends -- period_end's whole calendar day counts, not just up to
    # midnight at its start.
    start = datetime.combine(period_start, time.min, tzinfo=timezone.utc)
    end = datetime.combine(period_end, time.max, tzinfo=timezone.utc)
    return start, end


def _count(session: Session, *criteria) -> int:
    return session.execute(select(func.count()).where(*criteria)).scalar_one()


def get_tenant_usage(session: Session, tenant: Tenant, period_start: date, period_end: date) -> TenantUsage:
    start, end = _period_bounds(period_start, period_end)

    customers = _count(session, Customer.tenant_id == tenant.id)
    accounts = _count(session, Account.tenant_id == tenant.id)
    # Distinct human users, not total seat-memberships -- a person holding several
    # customer-scoped memberships in this one tenant still counts once.
    users = session.execute(
        select(func.count(func.distinct(TenantMembership.user_id))).where(
            TenantMembership.tenant_id == tenant.id, TenantMembership.is_active.is_(True)
        )
    ).scalar_one()

    paid_items_check = _count(session, PaidItem.tenant_id == tenant.id, PaidItem.created_at.between(start, end))
    paid_items_ach = _count(session, AchTransaction.tenant_id == tenant.id, AchTransaction.created_at.between(start, end))

    exceptions = _count(session, ExceptionItem.tenant_id == tenant.id, ExceptionItem.created_at.between(start, end))
    # Every finalized return counts regardless of Decision.source (HUMAN/AUTO_DEFAULT/
    # AUTO_ML) -- all three represent a real return; source is on the row already if the
    # billing system wants to segment further downstream.
    returns = _count(
        session, Decision.tenant_id == tenant.id, Decision.outcome == DecisionOutcome.RETURN, Decision.decided_at.between(start, end)
    )
    sms_notifications = _count(
        session, Notification.tenant_id == tenant.id, Notification.channel == NotificationChannel.SMS,
        Notification.status == NotificationStatus.SENT, Notification.sent_at.between(start, end),
    )
    bulk_uploads = _count(session, BulkUploadFile.tenant_id == tenant.id, BulkUploadFile.uploaded_at.between(start, end))

    return TenantUsage(
        tenant_id=tenant.id, tenant_slug=tenant.slug, tenant_name=tenant.name,
        customers=customers, accounts=accounts, users=users,
        paid_items_check=paid_items_check, paid_items_ach=paid_items_ach,
        paid_items_total=paid_items_check + paid_items_ach,
        exceptions=exceptions, returns=returns,
        sms_notifications=sms_notifications, bulk_uploads=bulk_uploads,
    )


def get_all_tenants_usage(session: Session, period_start: date, period_end: date) -> list[TenantUsage]:
    """The platform operator's view across every bank. Under Postgres RLS each bank's
    counts are read while bound to that bank."""
    tenants = session.execute(select(Tenant).order_by(Tenant.name)).scalars().all()
    usage = []
    for tenant in tenants:
        with acting_as_tenant(session, tenant.id):
            usage.append(get_tenant_usage(session, tenant, period_start, period_end))
    return usage
