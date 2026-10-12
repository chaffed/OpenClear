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


class InvalidCheckReturnReasonInput(ValueError):
    """Bad input; the message is fit to show."""


def _clean(reason_text: str, return_code: str | None) -> tuple[str, str | None]:
    text = (reason_text or "").strip()
    code = (return_code or "").strip().upper() or None
    if not text:
        raise InvalidCheckReturnReasonInput("The reason is required.")
    if len(text) > 255:
        raise InvalidCheckReturnReasonInput("The reason must be 255 characters or fewer.")
    if code is not None and (len(code) > 4 or not code.isalnum()):
        raise InvalidCheckReturnReasonInput("The return code is at most 4 letters or digits, such as N or Q.")
    return text, code


def create_check_return_reason(session: Session, tenant_id: uuid.UUID, *, reason_text: str, return_code: str | None) -> CheckReturnReason:
    text, code = _clean(reason_text, return_code)
    reason = CheckReturnReason(tenant_id=tenant_id, reason_text=text, return_code=code)
    session.add(reason)
    session.flush()
    return reason


def update_check_return_reason(session: Session, tenant_id: uuid.UUID, reason_id: uuid.UUID, *, reason_text: str,
                               return_code: str | None) -> CheckReturnReason | None:
    reason = get_check_return_reason(session, tenant_id, reason_id)
    if reason is None:
        return None
    reason.reason_text, reason.return_code = _clean(reason_text, return_code)
    session.flush()
    return reason


def set_active(session: Session, tenant_id: uuid.UUID, reason_id: uuid.UUID, active: bool) -> CheckReturnReason | None:
    reason = get_check_return_reason(session, tenant_id, reason_id)
    if reason is None:
        return None
    reason.is_active = active
    session.flush()
    return reason
