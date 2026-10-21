# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Per-tenant row counts for every table, for the Data Dictionary PDF's closing summary
(web/routers/docs.py). Reuses the same metadata-driven iteration over
`Base.metadata.sorted_tables` as `demo_tenant_service.py::_wipe_tenant_children` (branch
on which FK columns a table actually has), but for counting rather than deleting, and
extended to the handful of tables that have neither `tenant_id` nor `customer_id`."""

import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from openclear.db.base import Base
from openclear.domain.notification import NotificationPreference
from openclear.domain.tenant import Tenant
from openclear.domain.tenant_membership import TenantMembership
from openclear.domain.user import User

# Tables genuinely global/platform-wide, with no tenant or customer FK at all -- counted
# in full since they carry no tenant-sensitive data (see data-dictionary.html's own note
# on platform_api_key).
_GLOBAL_TABLE_NAMES = {"payment_network", "platform_api_key"}


def table_record_counts(db: Session, tenant_id: uuid.UUID) -> list[tuple[str, int]]:
    counts: list[tuple[str, int]] = []

    counts.append(("tenant", db.execute(select(func.count()).select_from(Tenant).where(Tenant.id == tenant_id)).scalar_one()))

    # `user` is explicitly global/cross-tenant (one identity can hold membership in more
    # than one tenant) -- scoped here via tenant_membership, not a tenant_id column of its
    # own.
    member_user_ids = select(TenantMembership.user_id).where(TenantMembership.tenant_id == tenant_id)
    counts.append(
        (
            "user",
            db.execute(select(func.count(func.distinct(User.id))).where(User.id.in_(member_user_ids))).scalar_one(),
        )
    )
    # notification_preference is a user-level setting (only has user_id), one hop further
    # than user itself -- this counts this tenant's member users' preference rows, which
    # is an approximation: a user in more than one tenant shares one preference row across
    # all of them, not something uniquely owned by this tenant.
    counts.append(
        (
            "notification_preference",
            db.execute(
                select(func.count()).select_from(NotificationPreference).where(NotificationPreference.user_id.in_(member_user_ids))
            ).scalar_one(),
        )
    )

    already_counted = {"tenant", "user", "notification_preference"}
    for table in Base.metadata.sorted_tables:
        if table.name in already_counted:
            continue
        if "tenant_id" in table.columns:
            counts.append((table.name, db.execute(select(func.count()).select_from(table).where(table.c.tenant_id == tenant_id)).scalar_one()))
        elif table.name in _GLOBAL_TABLE_NAMES:
            counts.append((table.name, db.execute(select(func.count()).select_from(table)).scalar_one()))

    return counts
