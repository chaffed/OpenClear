# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""A second person for settings changes (DUAL_CONTROL_PLAN.md; COMPLIANCE_PLAN.md C1 began it
for rules and models). Call sites use `request_or_apply`: a change that tightens, or loosens
something the scope doesn't protect, applies at once; otherwise it becomes a change request that
someone else who could have made it approves (services/change_kinds.py says who, and how to
apply it).

- **Who approves** (DC-D7): an active person other than the requester, holding the kind's
  permission, in the same scope: the bank's own staff for a bank's change; the customer's own
  people, or the bank's (`customer:manage`) when it has nobody else, for a customer's.
- **Nobody else** (DC-D6, DC-D17): the request applies itself after a 24-hour cooling-off
  unless withdrawn or rejected meanwhile, every admin is told, and applying it is a critical
  security event.
- **Turning a second-person switch on** (DC-D16) is refused unless someone else could approve
  each function it covers: `require_approvers`.
- One open request per setting; approval refused if the value changed since it was asked (DC-D8);
  requests expire after 7 days (DC-D9); a rejection needs a reason.

Every request, approval, rejection, withdrawal, expiry and cooling-off is in the action log and a
security event."""

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from openclear.domain.change_request import ChangeRequest, ChangeRequestStatus
from openclear.services import change_kinds
from openclear.services.change_kinds import ChangeError

EXPIRES_AFTER = timedelta(days=7)
COOLING_OFF = timedelta(hours=24)
PENDING = ChangeRequestStatus.PENDING.value

ChangeRequestError = ChangeError  # the name callers have used


@dataclass
class Outcome:
    applied: bool
    summary: str
    change: ChangeRequest | None = None

    @property
    def waiting(self) -> bool:
        return self.change is not None and not self.applied


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime | None) -> datetime | None:
    return None if value is None else (value if value.tzinfo else value.replace(tzinfo=timezone.utc))


# --- Who can approve --------------------------------------------------------------------------


def approvers(session: Session, tenant_id: uuid.UUID | None, customer_id: uuid.UUID | None, permission: str, *,
              excluding: uuid.UUID | None) -> list:
    """The active people who could approve a change needing `permission` in this scope, other
    than `excluding`. A customer's: its own people, or the bank's with customer:manage when it has
    nobody else."""
    from openclear.domain.security_group import SecurityGroup
    from openclear.domain.tenant_membership import TenantMembership
    from openclear.domain.user import User

    if tenant_id is None:
        return _platform_approvers(session, permission, excluding=excluding)

    def holders(scope_customer, needed) -> list:
        stmt = (select(User, SecurityGroup.permissions).join(TenantMembership, TenantMembership.user_id == User.id)
                .join(SecurityGroup, SecurityGroup.id == TenantMembership.security_group_id)
                .where(TenantMembership.tenant_id == tenant_id, TenantMembership.is_active.is_(True),
                       TenantMembership.pending_approval_at.is_(None), User.is_active.is_(True),
                       User.is_service_account.is_(False), User.platform_role.is_(None)))
        stmt = stmt.where(TenantMembership.customer_id == scope_customer) if scope_customer else stmt.where(TenantMembership.customer_id.is_(None))
        found, seen = [], set()
        for user, permissions in session.execute(stmt).all():
            if needed in (permissions or []) and user.id != excluding and user.id not in seen:
                seen.add(user.id)
                found.append(user)
        return found

    if customer_id is None:
        return holders(None, permission)
    return holders(customer_id, permission) or holders(None, "customer:manage")


def _platform_approvers(session: Session, permission: str, *, excluding: uuid.UUID | None) -> list:
    """The service provider's: platform staff with the role the change needs (DC4)."""
    from openclear.domain.user import User

    from openclear.services.support_access_service import PLATFORM_ROLES

    roles = {"operator"} if permission == "platform:operator" else set(PLATFORM_ROLES)
    return [u for u in session.execute(select(User).where(User.platform_role.in_(roles), User.is_active.is_(True))).scalars()
            if u.id != excluding]


def require_approvers(session: Session, tenant_id: uuid.UUID | None, customer_id: uuid.UUID | None,
                      functions: list[tuple[str, str]], *, user_id: uuid.UUID | None) -> None:
    """DC-D16: refuse to turn a second-person switch on unless, for each (function, permission),
    someone other than `user_id` could approve it."""
    missing = [(name, permission) for name, permission in functions
               if not approvers(session, tenant_id, customer_id, permission, excluding=user_id)]
    if missing:
        names = "; ".join(f"{name} (needs {permission})" for name, permission in missing)
        raise ChangeError(f"Nobody else can approve {names}. Give a second active person that permission first.")


# --- Asking ----------------------------------------------------------------------------------


def protected(session: Session, kind: change_kinds.Kind, tenant_id: uuid.UUID | None, customer_id: uuid.UUID | None) -> bool:
    """Whether a loosening change of this kind needs a second person in this scope."""
    from openclear.domain.tenant import Tenant

    if kind.tier == "protection" or tenant_id is None:
        return True
    tenant = session.get(Tenant, tenant_id)
    if customer_id is not None:
        return _customer_protected(session, tenant, customer_id)
    if kind.tier == "sensitive":
        return bool(tenant.sensitive_changes_need_approval)
    if kind.tier == "access":
        return bool(tenant.access_changes_need_approval)
    return False


def _customer_protected(session: Session, tenant, customer_id: uuid.UUID) -> bool:
    """A customer's sensitive changes, when the bank or the customer asks (DC-D10; DC3)."""
    from openclear.domain.customer import Customer

    customer = session.get(Customer, customer_id)
    return bool(getattr(tenant, "customer_changes_need_approval", False) or getattr(customer, "changes_need_approval", False))


def needs_approval(session: Session, tenant_id: uuid.UUID, kind_key: str, customer_id: uuid.UUID | None = None) -> bool:
    """Whether a loosening change of this kind would wait for a second person here."""
    return protected(session, change_kinds.get(kind_key), tenant_id, customer_id)


def open_request(session: Session, tenant_id, customer_id, setting: str) -> ChangeRequest | None:
    if not setting:
        return None
    stmt = select(ChangeRequest).where(ChangeRequest.setting == setting, ChangeRequest.status == PENDING)
    stmt = stmt.where(ChangeRequest.tenant_id == tenant_id) if tenant_id else stmt.where(ChangeRequest.tenant_id.is_(None))
    stmt = stmt.where(ChangeRequest.customer_id == customer_id) if customer_id else stmt.where(ChangeRequest.customer_id.is_(None))
    return session.execute(stmt).scalars().first()


def request_or_apply(session: Session, kind_key: str, payload: dict, *, tenant_id: uuid.UUID | None, customer_id: uuid.UUID | None = None,
                     user_id: uuid.UUID | None, channel: str = "web", summary: str | None = None, reason: str | None = None,
                     requested_with: str | None = None) -> Outcome:
    """Apply the change at once if it tightens or isn't protected here; otherwise ask for a
    second person. Raises ChangeError (fit to show) when it can't be either."""
    kind = change_kinds.get(kind_key)
    before = kind.current(session, tenant_id, customer_id, payload)
    after = {**before, **kind.value(payload)}
    if after == before:
        return Outcome(applied=True, summary="Nothing changed.")
    functions = kind.switches(session, tenant_id, customer_id, before, after)
    if functions:
        try:
            require_approvers(session, tenant_id, customer_id, functions, user_id=user_id)
        except ChangeError as exc:
            # Kept, though the change isn't made: the refusal is in the action log (DC-D16).
            session.rollback()
            if tenant_id is not None:
                from openclear.services import audit_log_service

                audit_log_service.record_action(session, tenant_id, actor_user_id=user_id, channel=channel,
                                                action="change_request.switch_refused", summary=f"Not turned on: {kind.title}. {exc}",
                                                resource_type="tenant", resource_id=tenant_id, metadata={"kind": kind.key})
                session.commit()
            raise
    summary = (summary or (kind.describe(before, after) if kind.describe else None) or kind.title)[:500]
    if not kind.loosens(before, after) or not protected(session, kind, tenant_id, customer_id):
        done = kind.apply(session, tenant_id, customer_id, payload, user_id)
        return Outcome(applied=True, summary=done or summary)
    setting = kind.setting(payload) or kind.key
    if open_request(session, tenant_id, customer_id, setting) is not None:
        raise ChangeError(f"A change to {kind.title.lower()} is already waiting for approval: approve, reject or withdraw it first.")
    now = _now()
    others = approvers(session, tenant_id, customer_id, kind.permission, excluding=user_id)
    change = ChangeRequest(
        tenant_id=tenant_id, customer_id=customer_id, scope="provider" if tenant_id is None else ("customer" if customer_id else "bank"),
        kind=kind.key, setting=setting, summary=summary, payload=payload, before=_jsonable(before), after=_jsonable(after),
        reason=(reason or "").strip()[:2000] or None, status=PENDING, requested_by_user_id=user_id,
        requested_with=requested_with or channel, requested_at=now, expires_at=now + EXPIRES_AFTER,
        applies_at=None if others else now + COOLING_OFF)
    session.add(change)
    session.flush()
    _record(session, change, user_id, channel, "change_request.create",
            f"Asked for a second approval: {summary}" + ("" if others else " (nobody else can approve: it applies in 24 hours unless withdrawn)"))
    _notify_requested(session, change, others)
    _webhook(session, change, "change_request.created")
    return Outcome(applied=False, summary=summary, change=change)


def _jsonable(value: dict) -> dict:
    return {k: (str(v) if isinstance(v, (uuid.UUID, datetime)) else v) for k, v in value.items()}


# --- Deciding --------------------------------------------------------------------------------


def get(session: Session, tenant_id: uuid.UUID | None, change_id: uuid.UUID) -> ChangeRequest | None:
    change = session.get(ChangeRequest, change_id)
    return change if change is not None and change.tenant_id == tenant_id else None


def pending(session: Session, tenant_id: uuid.UUID | None, *, customer_id: uuid.UUID | None = None, all_customers: bool = True) -> list[ChangeRequest]:
    stmt = select(ChangeRequest).where(ChangeRequest.status == PENDING)
    stmt = stmt.where(ChangeRequest.tenant_id == tenant_id) if tenant_id else stmt.where(ChangeRequest.tenant_id.is_(None))
    if customer_id is not None:
        stmt = stmt.where(ChangeRequest.customer_id == customer_id)
    elif not all_customers:
        stmt = stmt.where(ChangeRequest.customer_id.is_(None))
    return list(session.execute(stmt.order_by(ChangeRequest.requested_at)).scalars())


def can_decide(session: Session, change: ChangeRequest, user_id: uuid.UUID) -> bool:
    if change.requested_by_user_id == user_id:
        return False
    kind = change_kinds.get(change.kind)
    return any(u.id == user_id for u in approvers(session, change.tenant_id, change.customer_id, kind.permission,
                                                    excluding=change.requested_by_user_id))


def decide(session: Session, tenant_id: uuid.UUID | None, change_id: uuid.UUID, *, approve: bool, user_id: uuid.UUID,
           reason: str | None = None, channel: str = "web") -> ChangeRequest:
    from openclear.auth import sod

    change = get(session, tenant_id, change_id)
    if change is None or change.status != PENDING:
        raise ChangeError("That change isn't waiting for approval.")
    if change.requested_by_user_id == user_id:
        # Always enforced: approving your own change would make the second person pointless.
        violation = sod.Violation("author_approves", "Whoever asked for a change can't approve it")
        if tenant_id is not None:
            sod.record(session, tenant_id, user_id, violation, enforced=True, channel=channel, resource_type="change_request",
                       resource_id=change.id)
            session.commit()
        raise sod.SodViolation(violation)
    if not can_decide(session, change, user_id):
        raise ChangeError("You can't approve this change: it needs someone who could have made it.")
    if _aware(change.expires_at) and _aware(change.expires_at) < _now():
        _expire(session, change)
        raise ChangeError("This change expired before anyone decided.")
    if not approve and not (reason or "").strip():
        raise ChangeError("Say why you're rejecting it.")
    kind = change_kinds.get(change.kind)
    if approve:
        if _jsonable(kind.current(session, change.tenant_id, change.customer_id, change.payload)) != (change.before or {}):
            raise ChangeError("This setting changed since the change was asked for. Reject it and ask again.")
        result = kind.apply(session, change.tenant_id, change.customer_id, change.payload, user_id)
    change.status = (ChangeRequestStatus.APPROVED if approve else ChangeRequestStatus.REJECTED).value
    change.decided_by_user_id, change.decided_at = user_id, _now()
    change.decided_reason = (reason or "").strip()[:2000] or None
    _record(session, change, user_id, channel, "change_request.approve" if approve else "change_request.reject",
            f"Approved: {result or change.summary}" if approve else f"Rejected: {change.summary}. Why: {change.decided_reason}")
    if approve and kind.action:
        _record(session, change, user_id, channel, _action(kind, change),
                f"{result or change.summary} (asked by {_email(session, change.requested_by_user_id) or 'someone'}, approved by a second person)")
    _notify_decided(session, change)
    _webhook(session, change, "change_request.decided")
    session.flush()
    return change


def withdraw(session: Session, tenant_id: uuid.UUID | None, change_id: uuid.UUID, *, user_id: uuid.UUID, channel: str = "web") -> ChangeRequest:
    change = get(session, tenant_id, change_id)
    if change is None or change.status != PENDING:
        raise ChangeError("That change isn't waiting for approval.")
    if change.requested_by_user_id != user_id:
        raise ChangeError("Only whoever asked for a change can withdraw it.")
    change.status, change.decided_by_user_id, change.decided_at = ChangeRequestStatus.WITHDRAWN.value, user_id, _now()
    _record(session, change, user_id, channel, "change_request.withdraw", f"Withdrew: {change.summary}")
    session.flush()
    return change


# --- The clock: expiry and the cooling-off ------------------------------------------------------


def _expire(session: Session, change: ChangeRequest) -> None:
    change.status, change.decided_at = ChangeRequestStatus.EXPIRED.value, _now()
    _record(session, change, None, "system", "change_request.expire", f"Expired after 7 days: {change.summary}")
    session.flush()


def settle(session: Session, now: datetime | None = None) -> int:
    """Expire requests past 7 days, and apply cooling-off ones whose 24 hours are up. Every scope;
    binds to each bank in turn. Returns how many it settled."""
    from openclear.db.tenancy import bind_session_to_tenant
    from openclear.domain.tenant import Tenant

    now = now or _now()
    done = 0
    tenants = [None, *session.execute(select(Tenant.id).where(Tenant.is_active.is_(True))).scalars().all()]
    for tenant_id in tenants:
        bind_session_to_tenant(session, tenant_id) if tenant_id else None
        for change in pending(session, tenant_id):
            try:
                if _aware(change.applies_at) and _aware(change.applies_at) <= now:
                    _apply_alone(session, change)
                elif _aware(change.expires_at) and _aware(change.expires_at) <= now:
                    _expire(session, change)
                else:
                    continue
                session.commit()
                done += 1
            except ChangeError as exc:
                session.rollback()
                change = session.get(ChangeRequest, change.id)
                change.status, change.decided_at, change.decided_reason = ChangeRequestStatus.REJECTED.value, now, f"Couldn't apply: {exc}"
                _record(session, change, None, "system", "change_request.reject", f"Couldn't apply after the cooling-off: {exc}")
                session.commit()
    return done


def _apply_alone(session: Session, change: ChangeRequest) -> None:
    kind = change_kinds.get(change.kind)
    if _jsonable(kind.current(session, change.tenant_id, change.customer_id, change.payload)) != (change.before or {}):
        raise ChangeError("the setting changed since it was asked for")
    result = kind.apply(session, change.tenant_id, change.customer_id, change.payload, change.requested_by_user_id)
    change.status, change.decided_at = ChangeRequestStatus.APPLIED_ALONE.value, _now()
    _record(session, change, None, "system", "change_request.applied_alone",
            f"Applied without a second person, after 24 hours: {result or change.summary}")
    if kind.action:
        _record(session, change, change.requested_by_user_id, "system", _action(kind, change),
                f"{result or change.summary} (no second person: applied after 24 hours)")
    _notify_decided(session, change)
    _webhook(session, change, "change_request.decided")
    session.flush()


# --- Records, notices, webhooks ----------------------------------------------------------------


def _action(kind, change: ChangeRequest) -> str:
    return kind.action(change.payload) if callable(kind.action) else kind.action


def _record(session: Session, change: ChangeRequest, user_id, channel: str, action: str, summary: str) -> None:
    if change.tenant_id is None:
        _record_platform(session, change, user_id, action, summary)
        return
    from openclear.services import audit_log_service

    audit_log_service.record_action(session, change.tenant_id, actor_user_id=user_id, channel=channel, action=action, summary=summary,
                                    resource_type="change_request", resource_id=change.id, metadata={"kind": change.kind})


def _record_platform(session: Session, change: ChangeRequest, user_id, action: str, summary: str) -> None:
    """The service provider's changes go in the provider's own log (DC4)."""
    from openclear.services import platform_log_service

    platform_log_service.record(session, actor_user_id=user_id, action=action, summary=summary, resource_type="change_request",
                                resource_id=change.id, metadata={"kind": change.kind})


def _notify_requested(session: Session, change: ChangeRequest, others: list) -> None:
    from openclear.domain.notification import NotificationType
    from openclear.services import notification_service

    recipients = others
    if not others and change.tenant_id is not None:  # the cooling-off: every admin hears of it
        recipients = approvers(session, change.tenant_id, None, "tenant:manage", excluding=None)
    who = _email(session, change.requested_by_user_id)
    for user in recipients:
        notification_service._queue(session, tenant_id=change.tenant_id, recipient=user, notification_type=NotificationType.CHANGE_REQUESTED,
                                    template_context={"summary": change.summary, "who": who, "cooling_off": not others,
                                                      "applies_at": change.applies_at.strftime("%Y-%m-%d %H:%M UTC") if change.applies_at else None},
                                    resource_type="change_request", resource_id=change.id)


def _notify_decided(session: Session, change: ChangeRequest) -> None:
    from openclear.domain.notification import NotificationType
    from openclear.domain.user import User
    from openclear.services import notification_service

    requester = session.get(User, change.requested_by_user_id) if change.requested_by_user_id else None
    if requester is None:
        return
    notification_service._queue(session, tenant_id=change.tenant_id, recipient=requester, notification_type=NotificationType.CHANGE_DECIDED,
                                template_context={"summary": change.summary, "status": change.status, "who": _email(session, change.decided_by_user_id),
                                                  "reason": change.decided_reason},
                                resource_type="change_request", resource_id=change.id)


def _webhook(session: Session, change: ChangeRequest, event: str) -> None:
    if change.tenant_id is None:
        return
    from openclear.services import webhook_service

    webhook_service.emit(session, change.tenant_id, event, {"change_id": str(change.id), "kind": change.kind, "summary": change.summary,
                                                            "status": change.status, "customer_id": str(change.customer_id) if change.customer_id else None})


def _email(session: Session, user_id) -> str | None:
    from openclear.domain.user import User

    user = session.get(User, user_id) if user_id else None
    return user.email if user else None


# --- The two kinds from before, for their call sites -------------------------------------------


def request_model_activation(session: Session, tenant_id: uuid.UUID, model_id: uuid.UUID, *, customer_id: uuid.UUID | None,
                             user_id: uuid.UUID, channel: str = "web") -> Outcome:
    """Activate a model, or ask a second admin to, once it's confirmed to be one of this bank's
    (a bank-only model, or one of its customer's)."""
    from openclear.domain.ml_model import MlModel
    from openclear.services import customer_service

    model = session.get(MlModel, model_id)
    customer = customer_service.get_customer(session, tenant_id, customer_id) if customer_id else None
    if model is None or (customer_id and (customer is None or model.customer_id != customer_id)) \
            or (not customer_id and (model.tenant_id != tenant_id or model.customer_id is not None)):
        raise ChangeError("Model not found")
    summary = f"Activate {model.network_code} model {model.version}" + (f" for {customer.name}" if customer else " (bank-only)")
    return request_or_apply(session, "ml_model.activate", {"model_id": str(model_id), **({"customer_id": str(customer_id)} if customer_id else {})},
                            tenant_id=tenant_id, user_id=user_id, channel=channel, summary=summary)
