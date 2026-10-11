# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""The bank's list of reasons for returning a check (domain/check_return_reason.py)."""

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from openclear.domain.check_return_reason import CheckReturnReason

# Seeded for every bank: new ones at provisioning, existing ones by the migration that
# added the table (which keeps its own copy of this list). The codes are ANSI X9.100-188
# return reasons; a bank whose core uses others edits them.
DEFAULT_CHECK_RETURN_REASONS: list[tuple[str, str | None]] = [
    ("Altered or fictitious item", "N"),
    ("Not authorized", "Q"),
    ("Stop payment", "C"),
    ("Duplicate presentment", "Y"),
    ("Refer to maker", "S"),
    ("Stale dated", "G"),
    ("Signature irregular", "L"),
]


def seed_default_check_return_reasons(session: Session, tenant_id: uuid.UUID) -> list[CheckReturnReason]:
    reasons = [CheckReturnReason(tenant_id=tenant_id, reason_text=text, return_code=code) for text, code in DEFAULT_CHECK_RETURN_REASONS]
    session.add_all(reasons)
    session.flush()
    return reasons


def list_check_return_reasons(session: Session, tenant_id: uuid.UUID, *, active_only: bool = True) -> list[CheckReturnReason]:
    stmt = select(CheckReturnReason).where(CheckReturnReason.tenant_id == tenant_id)
    if active_only:
        stmt = stmt.where(CheckReturnReason.is_active.is_(True))
    return list(session.execute(stmt.order_by(CheckReturnReason.created_at, CheckReturnReason.reason_text)).scalars())


def get_check_return_reason(session: Session, tenant_id: uuid.UUID, reason_id: uuid.UUID) -> CheckReturnReason | None:
    reason = session.get(CheckReturnReason, reason_id)
    return reason if reason is not None and reason.tenant_id == tenant_id else None
