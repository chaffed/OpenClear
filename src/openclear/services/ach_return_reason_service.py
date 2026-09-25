# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from openclear.domain.ach_return_reason import AchReturnReason
from openclear.repositories.ach_return_reason_repo import AchReturnReasonRepository


class InvalidAchReturnReasonInput(ValueError):
    """Raised for a blank reason_text or a non-blank, non-digit transaction_code —
    callers turn this into a form error, never a 500."""


@dataclass(frozen=True, slots=True)
class AchReturnReasonInput:
    reason_text: str
    transaction_code: str | None


# Seeded once for every tenant — new ones via provisioning_service.py::create_tenant_with_admin,
# already-provisioned ones via the migration that introduced this table (both must stay
# in sync with this exact list; see that migration's own copy and comment).
# transaction_code is left blank for all of these: it's the bank's own core-system
# posting code (see domain/ach_return_reason.py), which only the bank's own admin knows.
DEFAULT_ACH_RETURN_REASONS: list[tuple[str, str | None]] = [
    ("Insufficient Funds", None),
    ("Account Closed", None),
    ("No Account/Unable to Locate Account", None),
    ("Customer Advises Not Authorized", None),
    ("Amount Not Same As Indicated", None),
    ("Payment Stopped", None),
    ("Uncollected Funds", None),
]


def _validate(data: AchReturnReasonInput) -> None:
    if not data.reason_text.strip():
        raise InvalidAchReturnReasonInput("Reason text is required")
    if data.transaction_code is not None and data.transaction_code.strip() and not data.transaction_code.strip().isdigit():
        raise InvalidAchReturnReasonInput(f"{data.transaction_code!r} is not a valid transaction code — digits only")


def seed_default_ach_return_reasons(session: Session, tenant_id: uuid.UUID) -> list[AchReturnReason]:
    """Called once when a tenant is created (provisioning_service.py), same "seed
    defaults, fully editable from there" pattern as
    security_group_service.py::seed_default_security_groups — otherwise a brand-new
    tenant couldn't return any ACH exception at all until an admin manually populated
    this catalog (see services/decision_service.py::decide(), which requires selecting
    one of these for an ACH RETURN)."""
    repo = AchReturnReasonRepository(session, tenant_id)
    reasons = [AchReturnReason(reason_text=text, transaction_code=code) for text, code in DEFAULT_ACH_RETURN_REASONS]
    for reason in reasons:
        repo.add(reason)
    session.flush()
    return reasons


def list_ach_return_reasons(
    session: Session,
    tenant_id: uuid.UUID,
    *,
    active_only: bool = False,
    limit: int | None = None,
    offset: int | None = None,
    order_by: Any = None,
) -> list[AchReturnReason]:
    repo = AchReturnReasonRepository(session, tenant_id)
    is_active = True if active_only else None
    return repo.list(is_active=is_active, limit=limit, offset=offset, order_by=order_by)


def get_ach_return_reason(session: Session, tenant_id: uuid.UUID, reason_id: uuid.UUID) -> AchReturnReason | None:
    return AchReturnReasonRepository(session, tenant_id).get(reason_id)


def create_ach_return_reason(session: Session, tenant_id: uuid.UUID, data: AchReturnReasonInput) -> AchReturnReason:
    _validate(data)
    reason = AchReturnReason(
        reason_text=data.reason_text.strip(), transaction_code=(data.transaction_code or "").strip() or None
    )
    AchReturnReasonRepository(session, tenant_id).add(reason)
    session.flush()
    return reason


def update_ach_return_reason(
    session: Session, tenant_id: uuid.UUID, reason_id: uuid.UUID, data: AchReturnReasonInput
) -> AchReturnReason | None:
    _validate(data)
    reason = AchReturnReasonRepository(session, tenant_id).get(reason_id)
    if reason is None:
        return None
    reason.reason_text = data.reason_text.strip()
    reason.transaction_code = (data.transaction_code or "").strip() or None
    session.flush()
    return reason


def deactivate_ach_return_reason(session: Session, tenant_id: uuid.UUID, reason_id: uuid.UUID) -> AchReturnReason | None:
    reason = AchReturnReasonRepository(session, tenant_id).get(reason_id)
    if reason is None:
        return None
    reason.is_active = False
    session.flush()
    return reason


def reactivate_ach_return_reason(session: Session, tenant_id: uuid.UUID, reason_id: uuid.UUID) -> AchReturnReason | None:
    reason = AchReturnReasonRepository(session, tenant_id).get(reason_id)
    if reason is None:
        return None
    reason.is_active = True
    session.flush()
    return reason
