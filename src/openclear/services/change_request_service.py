# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Two-person changes to how items are judged (COMPLIANCE_PLAN.md C1; the spec's "RuleAuthor
can't RuleDeploy"). Under segregation of duties in enforce mode, turning a matching-rule
plugin on or off and activating a scoring model become requests that a second person with
admin:manage approves; the person who asked can't. In warn mode they happen at once, as
before. Every request, approval and rejection is in the audit log."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from openclear.domain.change_request import ChangeRequest, ChangeRequestStatus

KINDS = {"matching_rule.set", "ml_model.activate"}


class ChangeRequestError(ValueError):
    pass


def needs_approval(session: Session, tenant_id: uuid.UUID) -> bool:
    from openclear.auth import sod

    return sod.mode(session, tenant_id) == "enforce"


def request_change(session: Session, tenant_id: uuid.UUID, *, kind: str, summary: str, payload: dict, user_id: uuid.UUID,
                   channel: str = "web") -> ChangeRequest:
    from openclear.services import audit_log_service

    assert kind in KINDS
    change = ChangeRequest(tenant_id=tenant_id, kind=kind, summary=summary[:500], payload=payload, requested_by_user_id=user_id,
                           status=ChangeRequestStatus.PENDING.value)
    session.add(change)
    session.flush()
    audit_log_service.record_action(session, tenant_id, actor_user_id=user_id, channel=channel, action="change_request.create",
                                    summary=f"Asked for a second approval: {summary}", resource_type="change_request", resource_id=change.id)
    return change


def request_model_activation(session: Session, tenant_id: uuid.UUID, model_id: uuid.UUID, *, customer_id: uuid.UUID | None,
                             user_id: uuid.UUID, channel: str = "web") -> ChangeRequest:
    """Asks for a second admin to activate a model, once it's confirmed to be one of this
    bank's (a bank-only model, or one of its customer's)."""
    from openclear.domain.ml_model import MlModel
    from openclear.services import customer_service

    model = session.get(MlModel, model_id)
    customer = customer_service.get_customer(session, tenant_id, customer_id) if customer_id else None
    if model is None or (customer_id and (customer is None or model.customer_id != customer_id)) \
            or (not customer_id and (model.tenant_id != tenant_id or model.customer_id is not None)):
        raise ChangeRequestError("Model not found")
    summary = f"Activate {model.network_code} model {model.version}" + (f" for {customer.name}" if customer else " (bank-only)")
    return request_change(session, tenant_id, kind="ml_model.activate", summary=summary,
                          payload={"model_id": str(model_id), **({"customer_id": str(customer_id)} if customer_id else {})},
                          user_id=user_id, channel=channel)


def pending(session: Session, tenant_id: uuid.UUID) -> list[ChangeRequest]:
    return list(session.execute(select(ChangeRequest).where(
        ChangeRequest.tenant_id == tenant_id, ChangeRequest.status == ChangeRequestStatus.PENDING.value,
    ).order_by(ChangeRequest.requested_at)).scalars())


def apply(session: Session, tenant_id: uuid.UUID, kind: str, payload: dict, user_id: uuid.UUID | None) -> str:
    """Makes the change itself (at once in warn mode, or on approval). Returns a summary."""
    from openclear.ml.registry import activate_model
    from openclear.services import customer_service, plugin_service

    if kind == "matching_rule.set":
        plugin_service.set_enabled(session, tenant_id, "matching_rules", payload["name"], bool(payload["enabled"]), user_id=user_id,
                                   config=payload.get("config") or None)
        return f"Matching rule {payload['name']} turned {'on' if payload['enabled'] else 'off'}"
    if kind == "ml_model.activate":
        customer_id = uuid.UUID(payload["customer_id"]) if payload.get("customer_id") else None
        if customer_id is not None and customer_service.get_customer(session, tenant_id, customer_id) is None:
            raise ChangeRequestError("That customer no longer exists.")
        model = activate_model(session, uuid.UUID(payload["model_id"]), expected_customer_id=customer_id,
                               **({} if customer_id else {"expected_tenant_id": tenant_id}))
        return f"Activated {model.network_code} model {model.version}"
    raise ChangeRequestError(f"Unknown change {kind}")


def decide(session: Session, tenant_id: uuid.UUID, change_id: uuid.UUID, *, approve: bool, user_id: uuid.UUID) -> ChangeRequest:
    from openclear.auth import sod
    from openclear.services import audit_log_service

    change = session.get(ChangeRequest, change_id)
    if change is None or change.tenant_id != tenant_id or change.status != ChangeRequestStatus.PENDING.value:
        raise ChangeRequestError("That change isn't waiting for approval.")
    if change.requested_by_user_id == user_id:
        # Always enforced: approving your own change would make the second person pointless.
        sod.record(session, tenant_id, user_id, sod.Violation("author_approves", "Whoever asked for a change can't approve it"),
                   enforced=True, channel="web", resource_type="change_request", resource_id=change.id)
        session.commit()
        raise sod.SodViolation(sod.Violation("author_approves", "Whoever asked for a change can't approve it"))
    if approve:
        result = apply(session, tenant_id, change.kind, change.payload, user_id)
    change.status = (ChangeRequestStatus.APPROVED if approve else ChangeRequestStatus.REJECTED).value
    change.decided_by_user_id, change.decided_at = user_id, datetime.now(timezone.utc)
    audit_log_service.record_action(
        session, tenant_id, actor_user_id=user_id, channel="web", action="change_request.approve" if approve else "change_request.reject",
        summary=(f"Approved: {result}" if approve else f"Rejected: {change.summary}"), resource_type="change_request", resource_id=change.id,
    )
    session.flush()
    return change
