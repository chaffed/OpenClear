# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import enum
import uuid
from dataclasses import dataclass

from sqlalchemy.orm import Session

from openclear.db.tenancy import TenantContext
from openclear.domain.ach_transaction import AchSettlementStatus, AchTransaction
from openclear.domain.decision import Decision, DecisionOutcome, DecisionSource
from openclear.domain.exception_item import ExceptionItem, ExceptionStatus
from openclear.domain.tenant import Tenant
from openclear.networks.registry import get_adapter
from openclear.repositories.ach_return_reason_repo import AchReturnReasonRepository
from openclear.repositories.decision_repo import DecisionRepository
from openclear.repositories.exception_repo import ExceptionRepository
from openclear.services import notification_service


class DecisionError(str, enum.Enum):
    NOT_FOUND = "not_found"
    ALREADY_DECIDED = "already_decided"
    RECOMMENDATION_REQUIRED = "recommendation_required"
    MAKER_CANNOT_APPROVE_OWN_RECOMMENDATION = "maker_cannot_approve_own_recommendation"
    WITHDRAWN = "withdrawn"
    # Only reachable for network_code == "ach" and outcome == RETURN — see
    # _resolve_ach_return_reason below. Every other network/outcome combination is
    # completely unaffected by these two.
    RETURN_REASON_REQUIRED = "return_reason_required"
    INVALID_RETURN_REASON = "invalid_return_reason"


@dataclass(frozen=True, slots=True)
class ServiceResult:
    decision: Decision | None
    exception: ExceptionItem | None
    error: DecisionError | None


@dataclass(frozen=True, slots=True)
class _ResolvedReason:
    reason_code: str
    transaction_code: str | None


def _resolve_ach_return_reason(
    session: Session,
    tenant_id: uuid.UUID,
    *,
    network_code: str,
    outcome: DecisionOutcome,
    reason_code: str,
    ach_return_reason_id: uuid.UUID | None,
) -> tuple[_ResolvedReason, DecisionError | None]:
    """An ACH exception being finalized as RETURN must select from the tenant's own
    AchReturnReason catalog rather than freeform text — see
    services/ach_return_reason_service.py. Every other network/outcome combination
    passes the given reason_code straight through unchanged, exactly as before this
    catalog existed."""
    if network_code != "ach" or outcome != DecisionOutcome.RETURN:
        return _ResolvedReason(reason_code=reason_code, transaction_code=None), None
    if ach_return_reason_id is None:
        return _ResolvedReason(reason_code=reason_code, transaction_code=None), DecisionError.RETURN_REASON_REQUIRED
    reason = AchReturnReasonRepository(session, tenant_id).get(ach_return_reason_id)
    if reason is None or not reason.is_active:
        return _ResolvedReason(reason_code=reason_code, transaction_code=None), DecisionError.INVALID_RETURN_REASON
    return _ResolvedReason(reason_code=reason.reason_text, transaction_code=reason.transaction_code), None


def submit_recommendation(
    session: Session,
    tenant_id: uuid.UUID,
    exception_id: uuid.UUID,
    ctx: TenantContext,
    *,
    outcome: DecisionOutcome,
    reason_code: str,
    notes: str | None,
    ach_return_reason_id: uuid.UUID | None = None,
) -> ServiceResult:
    repo = ExceptionRepository(session, tenant_id, ctx.customer_id)
    exception = repo.get(exception_id)
    if exception is None:
        return ServiceResult(None, None, DecisionError.NOT_FOUND)
    if exception.status == ExceptionStatus.WITHDRAWN:
        return ServiceResult(None, None, DecisionError.WITHDRAWN)
    if exception.status not in (ExceptionStatus.OPEN, ExceptionStatus.PENDING_APPROVAL):
        return ServiceResult(None, None, DecisionError.ALREADY_DECIDED)

    resolved, error = _resolve_ach_return_reason(
        session, tenant_id, network_code=exception.network_code, outcome=outcome, reason_code=reason_code,
        ach_return_reason_id=ach_return_reason_id,
    )
    if error is not None:
        return ServiceResult(None, None, error)

    exception.recommended_outcome = outcome.value
    exception.recommended_reason_code = resolved.reason_code
    exception.recommended_return_transaction_code = resolved.transaction_code
    exception.recommended_notes = notes
    exception.recommended_ach_return_reason_id = (
        ach_return_reason_id if exception.network_code == "ach" and outcome == DecisionOutcome.RETURN else None
    )
    exception.recommended_by_user_id = ctx.user_id
    exception.status = ExceptionStatus.PENDING_APPROVAL
    session.flush()
    notification_service.notify_recommendation_awaiting_approval(session, exception)
    return ServiceResult(None, exception, None)


def decide(
    session: Session,
    tenant_id: uuid.UUID,
    exception_id: uuid.UUID,
    ctx: TenantContext,
    *,
    outcome: DecisionOutcome,
    reason_code: str,
    notes: str | None,
    ach_return_reason_id: uuid.UUID | None = None,
) -> ServiceResult:
    """Records the final pay/return decision — this is the ML feedback point. Under
    dual-control tenants, requires a prior recommend() from a *different* user (maker !=
    checker is enforced here, not just in the UI). Finalizing an ACH RETURN also marks
    the underlying AchTransaction settled as RETURNED (see
    domain/ach_transaction.py::AchSettlementStatus) — previously never actually set
    anywhere, now the queryable signal services/wsud_service.py's eligibility check
    relies on."""
    tenant = session.get(Tenant, tenant_id)
    exception_repo = ExceptionRepository(session, tenant_id, ctx.customer_id)
    exception = exception_repo.get(exception_id)
    if exception is None:
        return ServiceResult(None, None, DecisionError.NOT_FOUND)
    if exception.status == ExceptionStatus.WITHDRAWN:
        return ServiceResult(None, None, DecisionError.WITHDRAWN)
    if exception.status in (ExceptionStatus.PAY, ExceptionStatus.RETURN):
        return ServiceResult(None, None, DecisionError.ALREADY_DECIDED)

    submitted_by_user_id: uuid.UUID | None = None
    if tenant.require_dual_control:
        if exception.status != ExceptionStatus.PENDING_APPROVAL:
            return ServiceResult(None, None, DecisionError.RECOMMENDATION_REQUIRED)
        if exception.recommended_by_user_id == ctx.user_id:
            return ServiceResult(None, None, DecisionError.MAKER_CANNOT_APPROVE_OWN_RECOMMENDATION)
        submitted_by_user_id = exception.recommended_by_user_id

    resolved, error = _resolve_ach_return_reason(
        session, tenant_id, network_code=exception.network_code, outcome=outcome, reason_code=reason_code,
        ach_return_reason_id=ach_return_reason_id,
    )
    if error is not None:
        return ServiceResult(None, None, error)

    adapter = get_adapter(exception.network_code)
    features = adapter.build_features(session, exception)

    decision_repo = DecisionRepository(session, tenant_id)
    decision = Decision(
        exception_item_id=exception.id,
        outcome=outcome,
        reason_code=resolved.reason_code,
        return_transaction_code=resolved.transaction_code,
        notes=notes,
        submitted_by_user_id=submitted_by_user_id,
        decided_by_user_id=ctx.user_id,
        source=DecisionSource.HUMAN,
        features_json=features,
    )
    decision_repo.add(decision)

    exception.status = ExceptionStatus.PAY if outcome == DecisionOutcome.PAY else ExceptionStatus.RETURN

    if outcome == DecisionOutcome.RETURN and exception.network_code == "ach":
        source_item = adapter.load_source_item(session, exception.source_item_id)
        if isinstance(source_item, AchTransaction):
            source_item.settlement_status = AchSettlementStatus.RETURNED

    session.flush()
    return ServiceResult(decision, exception, None)


def get_decision_for_exception(
    session: Session, tenant_id: uuid.UUID, exception_id: uuid.UUID, *, customer_id: uuid.UUID | None = None
) -> Decision | None:
    # Verify the exception is in scope first — a customer-scoped caller must not be able
    # to fetch another customer's decision just by knowing its exception_id.
    if ExceptionRepository(session, tenant_id, customer_id).get(exception_id) is None:
        return None
    repo = DecisionRepository(session, tenant_id)
    results = repo.list(exception_item_id=exception_id)
    return results[0] if results else None
