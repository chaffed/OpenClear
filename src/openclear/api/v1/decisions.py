# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from openclear.auth.deps import require_permission
from openclear.db.session import get_db
from openclear.db.tenancy import TenantContext
from openclear.schemas.decision import DecideRequest, DecisionRead, RecommendRequest
from openclear.schemas.exception import ExceptionRead
from openclear.services import audit_log_service, decision_service
from openclear.services.decision_service import DecisionError

router = APIRouter(prefix="/exceptions", tags=["decisions"])

_ERROR_STATUS = {
    DecisionError.NOT_FOUND: status.HTTP_404_NOT_FOUND,
    DecisionError.ALREADY_DECIDED: status.HTTP_409_CONFLICT,
    DecisionError.RECOMMENDATION_REQUIRED: status.HTTP_409_CONFLICT,
    DecisionError.MAKER_CANNOT_APPROVE_OWN_RECOMMENDATION: status.HTTP_403_FORBIDDEN,
    DecisionError.RETURN_REASON_REQUIRED: status.HTTP_422_UNPROCESSABLE_CONTENT,
    DecisionError.INVALID_RETURN_REASON: status.HTTP_422_UNPROCESSABLE_CONTENT,
    DecisionError.NOT_DECIDED: status.HTTP_404_NOT_FOUND,
    DecisionError.LOCKED: status.HTTP_409_CONFLICT,
    DecisionError.CHANGE_NEEDS_APPROVAL: status.HTTP_409_CONFLICT,
    DecisionError.WITHDRAWN: status.HTTP_409_CONFLICT,
}


def _read(decision, exception) -> DecisionRead:
    read = DecisionRead.model_validate(decision)
    if decision.locked_at is None and exception is not None:
        read.changeable_until = decision_service.changeable_until(exception)
    read.change_pending = bool(exception is not None and exception.change_recommended_at)
    return read


def _refuse(result) -> None:
    if result.error == DecisionError.LOCKED and result.decision is not None and result.decision.locked_at is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, f"locked: final since {result.decision.locked_at.isoformat()}")
    raise HTTPException(_ERROR_STATUS[result.error], result.error.value)


@router.post("/{exception_id}/recommend", response_model=ExceptionRead)
def recommend_decision(
    exception_id: uuid.UUID,
    payload: RecommendRequest,
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_permission("exception:recommend")),
) -> ExceptionRead:
    result = decision_service.submit_recommendation(
        db,
        ctx.tenant_id,
        exception_id,
        ctx,
        outcome=payload.outcome,
        reason_code=payload.reason_code,
        notes=payload.notes,
        ach_return_reason_id=payload.ach_return_reason_id,
    )
    if result.error is not None:
        raise HTTPException(_ERROR_STATUS[result.error], result.error.value)
    audit_log_service.record_action(
        db,
        ctx.tenant_id,
        actor_user_id=ctx.user_id,
        channel="api",
        action="exception.recommend",
        summary=f"Recommended {payload.outcome.value} on exception ({payload.reason_code})",
        resource_type="exception_item",
        resource_id=exception_id,
    )
    db.commit()
    return ExceptionRead.from_orm_row(result.exception)


@router.post("/{exception_id}/decide", response_model=DecisionRead)
def decide_exception(
    exception_id: uuid.UUID,
    payload: DecideRequest,
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_permission("exception:decide")),
) -> DecisionRead:
    result = decision_service.decide(
        db,
        ctx.tenant_id,
        exception_id,
        ctx,
        outcome=payload.outcome,
        reason_code=payload.reason_code,
        notes=payload.notes,
        ach_return_reason_id=payload.ach_return_reason_id,
    )
    if result.error is not None:
        raise HTTPException(_ERROR_STATUS[result.error], result.error.value)
    audit_log_service.record_action(
        db,
        ctx.tenant_id,
        actor_user_id=ctx.user_id,
        channel="api",
        action="exception.decide",
        summary=f"Decided {payload.outcome.value} on exception ({payload.reason_code})",
        resource_type="exception_item",
        resource_id=exception_id,
    )
    db.commit()
    return _read(result.decision, result.exception)


@router.get("/{exception_id}/decision", response_model=DecisionRead)
def get_decision(
    exception_id: uuid.UUID,
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_permission("exception:read")),
) -> DecisionRead:
    decision = decision_service.get_decision_for_exception(db, ctx.tenant_id, exception_id, customer_id=ctx.customer_id)
    if decision is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No decision recorded for this exception")
    from openclear.repositories.exception_repo import ExceptionRepository

    return _read(decision, ExceptionRepository(db, ctx.tenant_id, ctx.customer_id).get(exception_id))


@router.put("/{exception_id}/decision", response_model=DecisionRead)
def change_decision(
    exception_id: uuid.UUID,
    payload: DecideRequest,
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_permission("exception:read")),
) -> DecisionRead:
    """Change a decision before its item's cut-off (`changeable_until`); afterwards it's final
    and this answers 409. It follows a first decision's rules. Under dual control, someone with
    `exception:recommend` proposes the change (`change_pending` is then true and the decision
    in place stands), and someone else with `exception:decide` approves it the same way."""
    from openclear.domain.tenant import Tenant
    from openclear.repositories.exception_repo import ExceptionRepository

    exception = ExceptionRepository(db, ctx.tenant_id, ctx.customer_id).get(exception_id)
    dual = db.get(Tenant, ctx.tenant_id).require_dual_control
    proposing = dual and exception is not None and exception.change_recommended_at is None
    needed = "exception:recommend" if proposing else "exception:decide"
    if needed not in ctx.permissions:
        raise HTTPException(status.HTTP_403_FORBIDDEN, f"Requires {needed}")
    service = decision_service.recommend_change if proposing else decision_service.change_decision
    kwargs = {} if proposing else {"channel": "api"}
    result = service(db, ctx.tenant_id, exception_id, ctx, outcome=payload.outcome, reason_code=payload.reason_code,
                     notes=payload.notes, ach_return_reason_id=payload.ach_return_reason_id, **kwargs)
    if result.error is not None:
        _refuse(result)
    action, verb = ("exception.recommend_change", "Recommended changing") if proposing else ("exception.change_decision", "Changed")
    audit_log_service.record_action(
        db, ctx.tenant_id, actor_user_id=ctx.user_id, channel="api", action=action,
        summary=f"{verb} the decision to {payload.outcome.value} before the cut-off ({payload.reason_code})",
        resource_type="exception_item", resource_id=exception_id,
    )
    db.commit()
    return _read(result.decision, result.exception)


@router.delete("/{exception_id}/decision", status_code=status.HTTP_204_NO_CONTENT)
def undo_decision(
    exception_id: uuid.UUID,
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_permission("exception:decide")),
) -> None:
    """Take a decision back before its item's cut-off: the item is open to decide again. Not
    under dual control (409), where a change is recommended and approved instead."""
    result = decision_service.undo_decision(db, ctx.tenant_id, exception_id, ctx)
    if result.error is not None:
        _refuse(result)
    audit_log_service.record_action(
        db, ctx.tenant_id, actor_user_id=ctx.user_id, channel="api", action="exception.undo_decision",
        summary="Took the decision back before the cut-off", resource_type="exception_item", resource_id=exception_id,
    )
    db.commit()
