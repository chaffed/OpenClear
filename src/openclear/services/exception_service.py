# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from openclear.domain.exception_item import ExceptionItem, ExceptionStatus
from openclear.repositories.exception_repo import ExceptionRepository


# What the web queue shows by default: everything still waiting on a person.
NEEDS_ATTENTION = (ExceptionStatus.OPEN, ExceptionStatus.PENDING_APPROVAL)


def _query(session: Session, tenant_id: uuid.UUID, *, network_code, statuses, customer_id):
    stmt = ExceptionRepository(session, tenant_id, customer_id).query()
    if network_code is not None:
        stmt = stmt.where(ExceptionItem.network_code == network_code)
    if statuses:
        stmt = stmt.where(ExceptionItem.status.in_(list(statuses)))
    return stmt


def list_exceptions(
    session: Session,
    tenant_id: uuid.UUID,
    *,
    network_code: str | None = None,
    status: ExceptionStatus | None = None,
    statuses: tuple[ExceptionStatus, ...] | None = None,
    customer_id: uuid.UUID | None = None,
    limit: int | None = None,
    offset: int | None = None,
    order_by: Any = None,
) -> list[ExceptionItem]:
    """`status` filters to one status; `statuses` to any of several (e.g. NEEDS_ATTENTION)."""
    stmt = _query(session, tenant_id, network_code=network_code, statuses=statuses or ((status,) if status else None),
                  customer_id=customer_id)
    if order_by is not None:
        stmt = stmt.order_by(*order_by) if isinstance(order_by, (list, tuple)) else stmt.order_by(order_by)
    if offset is not None:
        stmt = stmt.offset(offset)
    if limit is not None:
        stmt = stmt.limit(limit)
    return list(session.execute(stmt).scalars().all())


def count_exceptions(
    session: Session,
    tenant_id: uuid.UUID,
    *,
    network_code: str | None = None,
    statuses: tuple[ExceptionStatus, ...] | None = None,
    customer_id: uuid.UUID | None = None,
) -> int:
    stmt = _query(session, tenant_id, network_code=network_code, statuses=statuses, customer_id=customer_id)
    return session.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()


def get_exception(
    session: Session, tenant_id: uuid.UUID, exception_id: uuid.UUID, *, customer_id: uuid.UUID | None = None
) -> ExceptionItem | None:
    repo = ExceptionRepository(session, tenant_id, customer_id)
    return repo.get(exception_id)
