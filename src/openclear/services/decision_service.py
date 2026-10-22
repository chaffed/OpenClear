# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import enum
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from openclear.db.tenancy import TenantContext
from openclear.domain.ach_transaction import AchSettlementStatus, AchTransaction
from openclear.domain.decision import Decision, DecisionOutcome, DecisionRevision, DecisionSource
from openclear.domain.exception_item import ExceptionItem, ExceptionStatus
from openclear.domain.tenant import Tenant
from openclear.networks.registry import get_adapter
from openclear.repositories.ach_return_reason_repo import AchReturnReasonRepository
from openclear.repositories.decision_repo import DecisionRepository
from openclear.repositories.exception_repo import ExceptionRepository
from openclear.services import decision_feed_service, notification_service, webhook_service

logger = logging.getLogger(__name__)


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
    # withdraw_recommendation: only its maker, only while it awaits approval.
    NOT_PENDING_APPROVAL = "not_pending_approval"
    NOT_YOUR_RECOMMENDATION = "not_your_recommendation"
    # Changing a decision (UI_PLAN.md UI-D13): only before its cut-off, while it isn't final.
    NOT_DECIDED = "not_decided"
    LOCKED = "locked"
    # Under dual control a change is recommended, and someone else approves it.
    CHANGE_NEEDS_APPROVAL = "change_needs_approval"


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
    check_return_reason_id: uuid.UUID | None = None,
) -> tuple[_ResolvedReason, DecisionError | None]:
    """An ACH exception being finalized as RETURN must select from the tenant's own
    AchReturnReason catalog rather than freeform text — see
    services/ach_return_reason_service.py. Every other network/outcome combination
    passes the given reason_code straight through unchanged, exactly as before this
    catalog existed."""
    if network_code == "check" and outcome == DecisionOutcome.RETURN and check_return_reason_id is not None:
        # A check returned from a web page names one of the bank's check return reasons
        # (domain/check_return_reason.py); its code goes where an ACH reason's would. The
        # API may still send free text, as before.
        from openclear.services import check_return_reason_service

        check_reason = check_return_reason_service.get_check_return_reason(session, tenant_id, check_return_reason_id)
        if check_reason is None or not check_reason.is_active:
            return _ResolvedReason(reason_code=reason_code, transaction_code=None), DecisionError.INVALID_RETURN_REASON
        return _ResolvedReason(reason_code=check_reason.reason_text, transaction_code=check_reason.return_code), None
    if network_code != "ach" or outcome != DecisionOutcome.RETURN:
        return _ResolvedReason(reason_code=reason_code, transaction_code=None), None
    if ach_return_reason_id is None:
        return _ResolvedReason(reason_code=reason_code, transaction_code=None), DecisionError.RETURN_REASON_REQUIRED
    reason = AchReturnReasonRepository(session, tenant_id).get(ach_return_reason_id)
    if reason is None or not reason.is_active:
        return _ResolvedReason(reason_code=reason_code, transaction_code=None), DecisionError.INVALID_RETURN_REASON
    return _ResolvedReason(reason_code=reason.reason_text, transaction_code=reason.transaction_code), None


def _locked(session: Session, tenant_id: uuid.UUID, exception_id: uuid.UUID, ctx: TenantContext) -> ExceptionItem | None:
    """The item in the caller's scope, locked until the transaction ends (FULL_REVIEW_PLAN.md
    FR-D1). Every entry point below starts here, so its checks read what it will change."""
    return ExceptionRepository(session, tenant_id, ctx.customer_id).get_for_update(exception_id)


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
    check_return_reason_id: uuid.UUID | None = None,
) -> ServiceResult:
    exception = _locked(session, tenant_id, exception_id, ctx)
    if exception is None:
        return ServiceResult(None, None, DecisionError.NOT_FOUND)
    if exception.status == ExceptionStatus.WITHDRAWN:
        return ServiceResult(None, None, DecisionError.WITHDRAWN)
    if exception.status not in (ExceptionStatus.OPEN, ExceptionStatus.PENDING_APPROVAL):
        return ServiceResult(None, None, DecisionError.ALREADY_DECIDED)

    resolved, error = _resolve_ach_return_reason(
        session, tenant_id, network_code=exception.network_code, outcome=outcome, reason_code=reason_code,
        ach_return_reason_id=ach_return_reason_id, check_return_reason_id=check_return_reason_id,
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


def withdraw_recommendation(session: Session, tenant_id: uuid.UUID, exception_id: uuid.UUID, ctx: TenantContext) -> ServiceResult:
    """Takes back one's own recommendation before anyone has approved it (MOBILE_PLAN.md R2: a
    phone's Undo): the exception is open again, as if it had never been recommended. A
    decision is never undone, and nobody withdraws someone else's recommendation."""
    exception = _locked(session, tenant_id, exception_id, ctx)
    if exception is None:
        return ServiceResult(None, None, DecisionError.NOT_FOUND)
    if exception.status != ExceptionStatus.PENDING_APPROVAL:
        return ServiceResult(None, None, DecisionError.NOT_PENDING_APPROVAL)
    if exception.recommended_by_user_id != ctx.user_id:
        return ServiceResult(None, None, DecisionError.NOT_YOUR_RECOMMENDATION)
    exception.recommended_outcome = None
    exception.recommended_reason_code = None
    exception.recommended_return_transaction_code = None
    exception.recommended_notes = None
    exception.recommended_ach_return_reason_id = None
    exception.recommended_by_user_id = None
    exception.status = ExceptionStatus.OPEN
    session.flush()
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
    check_return_reason_id: uuid.UUID | None = None,
    channel: str = "api",
) -> ServiceResult:
    """Records the final pay/return decision — this is the ML feedback point. Under
    dual-control tenants, requires a prior recommend() from a *different* user (maker !=
    checker is enforced here, not just in the UI). Finalizing an ACH RETURN also marks
    the underlying AchTransaction settled as RETURNED (see
    domain/ach_transaction.py::AchSettlementStatus) — previously never actually set
    anywhere, now the queryable signal services/wsud_service.py's eligibility check
    relies on."""
    tenant = session.get(Tenant, tenant_id)
    exception = _locked(session, tenant_id, exception_id, ctx)
    if exception is None:
        return ServiceResult(None, None, DecisionError.NOT_FOUND)
    if exception.status == ExceptionStatus.WITHDRAWN:
        return ServiceResult(None, None, DecisionError.WITHDRAWN)
    if exception.status in (ExceptionStatus.PAY, ExceptionStatus.RETURN):
        return ServiceResult(None, None, DecisionError.ALREADY_DECIDED)
    if DecisionRepository(session, tenant_id).get_by_exception(exception.id) is not None:
        # Can't happen with the lock above; if a row somehow exists anyway, say so plainly
        # rather than fail on the unique constraint.
        return ServiceResult(None, None, DecisionError.ALREADY_DECIDED)

    # Segregation of duties (auth/sod.py): raises SodViolation in enforce mode, logs either way.
    from openclear.auth import sod

    sod.check_decision(session, tenant_id, ctx, exception, channel=channel)

    submitted_by_user_id: uuid.UUID | None = None
    if tenant.require_dual_control:
        if exception.status != ExceptionStatus.PENDING_APPROVAL:
            return ServiceResult(None, None, DecisionError.RECOMMENDATION_REQUIRED)
        if exception.recommended_by_user_id == ctx.user_id:
            return ServiceResult(None, None, DecisionError.MAKER_CANNOT_APPROVE_OWN_RECOMMENDATION)
        submitted_by_user_id = exception.recommended_by_user_id

    resolved, error = _resolve_ach_return_reason(
        session, tenant_id, network_code=exception.network_code, outcome=outcome, reason_code=reason_code,
        ach_return_reason_id=ach_return_reason_id, check_return_reason_id=check_return_reason_id,
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
    # Changeable until the item's cut-off (UI_PLAN.md UI-D13); with none ahead, final now.
    if changeable_until(exception) is None:
        lock(session, decision, exception)
    session.flush()
    return ServiceResult(decision, exception, None)


# --- Changeable until the cut-off (UI_PLAN.md UI-D13) ---------------------------------------------


def _aware(value: datetime | None) -> datetime | None:
    return value.replace(tzinfo=timezone.utc) if value is not None and value.tzinfo is None else value


def changeable_until(exception: ExceptionItem, now: datetime | None = None) -> datetime | None:
    """The item's cut-off, if it's still ahead: until then its decision can be changed."""
    deadline = _aware(exception.decision_deadline)
    return deadline if deadline is not None and deadline > (now or datetime.now(timezone.utc)) else None


def lock(session: Session, decision: Decision, exception: ExceptionItem) -> None:
    """Makes a decision final and sends it out: its number in the decision feed (so the
    bank's core and the decision files see it), the exception.decided webhook, and an ACH
    return's settlement. Nothing leaves OpenClear before this."""
    if decision.locked_at is not None:
        return
    decision.locked_at = datetime.now(timezone.utc)
    decision_feed_service.assign_sequence(session, decision, exception)
    webhook_service.emit(session, exception.tenant_id, "exception.decided", {
        "exception_id": exception.id, "decision_id": decision.id, "sequence": decision.feed_sequence, "outcome": decision.outcome.value,
    })
    if decision.outcome == DecisionOutcome.RETURN and exception.network_code == "ach":
        source_item = get_adapter(exception.network_code).load_source_item(session, exception.source_item_id)
        if isinstance(source_item, AchTransaction):
            source_item.settlement_status = AchSettlementStatus.RETURNED
    if exception.change_recommended_at is not None:
        # A change nobody approved by the cut-off: the decision already made is what locks.
        _restore_recommendation(session, exception, decision)
    session.flush()


def _restore_recommendation(session: Session, exception: ExceptionItem, decision: Decision) -> None:
    """Puts back what was recommended for the decision in place, over a change recommended for it."""
    exception.change_recommended_at = None
    exception.recommended_outcome = decision.outcome.value
    exception.recommended_reason_code = decision.reason_code
    exception.recommended_return_transaction_code = decision.return_transaction_code
    exception.recommended_notes = decision.notes
    exception.recommended_by_user_id = decision.submitted_by_user_id


def _revise(session: Session, decision: Decision, exception: ExceptionItem, user_id: uuid.UUID | None, *, undone: bool) -> None:
    session.add(DecisionRevision(
        tenant_id=exception.tenant_id, exception_item_id=exception.id, outcome=decision.outcome, reason_code=decision.reason_code,
        return_transaction_code=decision.return_transaction_code, notes=decision.notes,
        submitted_by_user_id=decision.submitted_by_user_id, decided_by_user_id=decision.decided_by_user_id,
        decided_at=decision.decided_at, revised_by_user_id=user_id, undone=undone))


def _changeable_decision(session: Session, tenant_id: uuid.UUID, exception_id: uuid.UUID, ctx: TenantContext
                         ) -> tuple[ExceptionItem | None, Decision | None, DecisionError | None]:
    exception = _locked(session, tenant_id, exception_id, ctx)
    if exception is None:
        return None, None, DecisionError.NOT_FOUND
    decision = DecisionRepository(session, tenant_id).get_by_exception(exception.id) if exception.status in (
        ExceptionStatus.PAY, ExceptionStatus.RETURN) else None
    if decision is None:
        return exception, None, DecisionError.NOT_DECIDED
    if decision.locked_at is not None or changeable_until(exception) is None:
        return exception, decision, DecisionError.LOCKED
    return exception, decision, None


def change_decision(
    session: Session, tenant_id: uuid.UUID, exception_id: uuid.UUID, ctx: TenantContext, *, outcome: DecisionOutcome,
    reason_code: str, notes: str | None, ach_return_reason_id: uuid.UUID | None = None,
    check_return_reason_id: uuid.UUID | None = None, channel: str = "api",
) -> ServiceResult:
    """Changes a decision before its cut-off, by the rules a first decision follows: a return
    needs a reason, segregation of duties is checked again, and under dual control the change
    is recommended for someone else to approve (`recommend_change`). The earlier version is
    kept (decision_revision); the decision itself is updated, so it stays one per item."""
    exception, decision, error = _changeable_decision(session, tenant_id, exception_id, ctx)
    if error is not None:
        return ServiceResult(decision, exception, error)
    tenant = session.get(Tenant, tenant_id)
    if tenant.require_dual_control and exception.change_recommended_at is None:
        return ServiceResult(decision, exception, DecisionError.CHANGE_NEEDS_APPROVAL)
    from openclear.auth import sod

    sod.check_decision(session, tenant_id, ctx, exception, channel=channel)
    submitted_by_user_id = decision.submitted_by_user_id
    if tenant.require_dual_control:  # approving a recommended change
        if exception.recommended_by_user_id == ctx.user_id:
            return ServiceResult(decision, exception, DecisionError.MAKER_CANNOT_APPROVE_OWN_RECOMMENDATION)
        submitted_by_user_id = exception.recommended_by_user_id
    resolved, error = _resolve_ach_return_reason(
        session, tenant_id, network_code=exception.network_code, outcome=outcome, reason_code=reason_code,
        ach_return_reason_id=ach_return_reason_id, check_return_reason_id=check_return_reason_id)
    if error is not None:
        return ServiceResult(decision, exception, error)
    _revise(session, decision, exception, ctx.user_id, undone=False)
    decision.outcome, decision.reason_code, decision.return_transaction_code = outcome, resolved.reason_code, resolved.transaction_code
    decision.notes, decision.submitted_by_user_id, decision.decided_by_user_id = notes, submitted_by_user_id, ctx.user_id
    decision.decided_at, decision.source = datetime.now(timezone.utc), DecisionSource.HUMAN
    exception.status = ExceptionStatus.PAY if outcome == DecisionOutcome.PAY else ExceptionStatus.RETURN
    exception.change_recommended_at = None
    session.flush()
    return ServiceResult(decision, exception, None)


def recommend_change(
    session: Session, tenant_id: uuid.UUID, exception_id: uuid.UUID, ctx: TenantContext, *, outcome: DecisionOutcome,
    reason_code: str, notes: str | None, ach_return_reason_id: uuid.UUID | None = None,
    check_return_reason_id: uuid.UUID | None = None,
) -> ServiceResult:
    """Under dual control: proposes a change to a decision before its cut-off. The decision
    already made stands until someone else approves the change (change_decision); a change
    nobody approves by the cut-off is dropped and the decision in place locks."""
    exception, decision, error = _changeable_decision(session, tenant_id, exception_id, ctx)
    if error is not None:
        return ServiceResult(decision, exception, error)
    resolved, error = _resolve_ach_return_reason(
        session, tenant_id, network_code=exception.network_code, outcome=outcome, reason_code=reason_code,
        ach_return_reason_id=ach_return_reason_id, check_return_reason_id=check_return_reason_id)
    if error is not None:
        return ServiceResult(decision, exception, error)
    exception.recommended_outcome = outcome.value
    exception.recommended_reason_code = resolved.reason_code
    exception.recommended_return_transaction_code = resolved.transaction_code
    exception.recommended_notes = notes
    exception.recommended_ach_return_reason_id = (
        ach_return_reason_id if exception.network_code == "ach" and outcome == DecisionOutcome.RETURN else None)
    exception.recommended_by_user_id = ctx.user_id
    exception.change_recommended_at = datetime.now(timezone.utc)
    session.flush()
    notification_service.notify_recommendation_awaiting_approval(session, exception)
    return ServiceResult(decision, exception, None)


def undo_decision(session: Session, tenant_id: uuid.UUID, exception_id: uuid.UUID, ctx: TenantContext) -> ServiceResult:
    """Takes a decision back before its cut-off (the toast's Undo): the item is open to decide
    again, and the decision is kept as an undone revision. Not under dual control, where a
    change goes through approval instead."""
    exception, decision, error = _changeable_decision(session, tenant_id, exception_id, ctx)
    if error is not None:
        return ServiceResult(decision, exception, error)
    if session.get(Tenant, tenant_id).require_dual_control:
        return ServiceResult(decision, exception, DecisionError.CHANGE_NEEDS_APPROVAL)
    _revise(session, decision, exception, ctx.user_id, undone=True)
    session.delete(decision)
    exception.status = ExceptionStatus.OPEN
    exception.change_recommended_at = None
    session.flush()
    return ServiceResult(None, exception, None)


def revisions(session: Session, tenant_id: uuid.UUID, exception_id: uuid.UUID) -> list[DecisionRevision]:
    return list(session.execute(select(DecisionRevision).where(
        DecisionRevision.tenant_id == tenant_id, DecisionRevision.exception_item_id == exception_id)
        .order_by(DecisionRevision.revised_at)).scalars())


def lock_due(session: Session, now: datetime | None = None) -> int:
    """At the cut-off (workers/tasks.py::decision_lock_job, and the deadline sweep): locks every
    decision whose item's deadline has passed, across banks, committing each on its own so one
    failure doesn't hold the rest back (FULL_REVIEW_PLAN.md FR-D5); a failure rolls the session
    back, so call it with nothing of your own uncommitted. Each item is taken with
    the same row lock a person changing it takes, skipping one that's busy (the next run gets
    it), and checked again once held: a decision taken back a moment before isn't sent.
    Returns how many locked."""
    from openclear.db.tenancy import bind_session_to_tenant

    now = now or datetime.now(timezone.utc)
    due = session.execute(select(ExceptionItem.id, ExceptionItem.tenant_id).join(
        Decision, Decision.exception_item_id == ExceptionItem.id).where(
        Decision.locked_at.is_(None), ExceptionItem.decision_deadline.is_not(None), ExceptionItem.decision_deadline <= now)).all()
    locked = 0
    for exception_id, tenant_id in due:
        bind_session_to_tenant(session, tenant_id)
        try:
            exception = ExceptionRepository(session, tenant_id).get_for_update(exception_id, skip_locked=True)
            decision = DecisionRepository(session, tenant_id).get_by_exception(exception_id) if exception is not None else None
            deadline = _aware(exception.decision_deadline) if exception is not None else None
            if decision is None or decision.locked_at is not None or deadline is None or deadline > now:
                continue  # busy, taken back or already final; any row lock held ends with the next commit
            lock(session, decision, exception)
            session.commit()
            locked += 1
        except Exception:  # noqa: BLE001 -- one item's failure never holds the others; tried again next run
            session.rollback()
            logger.exception("Locking the decision on exception %s failed", exception_id)
    return locked


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
