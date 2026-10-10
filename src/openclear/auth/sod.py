# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Segregation of duties (COMPLIANCE_PLAN.md C1; FDIC internal control guidelines, NCUA
Part 715): duties that one person mustn't hold together, checked in code rather than left
to how a bank happens to set up its groups.

**Rules about permissions:** a security group shouldn't hold both sides of a rule. In
`enforce` mode it can't be saved that way, and a person holding both is refused the
deciding side.

**Rules about transactions:** whoever presented an item (submitted the paid item, check
image or ACH debit, through the API, a file or the web) can't decide its exception.

**Changes to rules and models** need a second person in `enforce` mode
(services/change_request_service.py).

A breach attempt is always written to the audit log at once (`sod.violation`). In `enforce`
mode it's refused with 403 and `ERR_SOD_VIOLATION`. In `warn` mode (every bank's starting
point, so an upgrade can't lock a small bank out) it goes ahead."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

ERROR_CODE = "ERR_SOD_VIOLATION"
INGEST = frozenset({"paid_item:write", "issued_item:write", "ach_transaction:write", "check_image:write"})


@dataclass(frozen=True)
class Rule:
    key: str
    title: str
    left: frozenset[str]
    right: frozenset[str]
    why: str


RULES: tuple[Rule, ...] = (
    Rule("ingest_vs_decide", "Whoever submits items can't decide them", INGEST, frozenset({"exception:decide"}),
         "Someone who can put items in and decide them could approve an item they made up."),
    Rule("users_vs_permissions", "Whoever manages people can't define what permissions grant",
         frozenset({"user:manage"}), frozenset({"security_group:manage"}),
         "Someone who can do both could give themselves, or anyone, any power."),
)


@dataclass(frozen=True)
class Violation:
    rule: str
    message: str


class SodViolation(Exception):
    """Raised (enforce mode) when an action breaks segregation of duties."""

    def __init__(self, violation: Violation) -> None:
        super().__init__(violation.message)
        self.violation = violation

    def body(self) -> dict:
        return {"code": ERROR_CODE, "rule": self.violation.rule, "message": self.violation.message}


def group_conflicts(permissions) -> list[Rule]:
    """The rules a set of permissions breaks."""
    held = set(permissions)
    return [r for r in RULES if held & r.left and held & r.right]


def mode(session: Session, tenant_id: uuid.UUID) -> str:
    from openclear.domain.tenant import Tenant

    tenant = session.get(Tenant, tenant_id)
    return tenant.sod_mode if tenant is not None else "warn"


def record(session: Session, tenant_id: uuid.UUID, actor_user_id: uuid.UUID | None, violation: Violation, *,
           enforced: bool, channel: str = "api", resource_type: str = "user", resource_id: uuid.UUID | None = None) -> None:
    """The audit entry, written at once (its own savepoint, committed by the caller or by
    breach() below) so a refused request still leaves its trace."""
    from openclear.services import audit_log_service

    audit_log_service.record_action(
        session, tenant_id, actor_user_id=actor_user_id, channel=channel if channel in ("web", "api") else "api",
        action="sod.violation", summary=f"{'Refused' if enforced else 'Allowed (warn mode)'}: {violation.message}",
        resource_type=resource_type, resource_id=resource_id or actor_user_id,
        metadata={"rule": violation.rule, "enforced": enforced, "severity": "critical" if enforced else "warning"},
    )


def breach(session: Session, tenant_id: uuid.UUID, actor_user_id: uuid.UUID | None, violation: Violation, *,
           channel: str = "api", resource_type: str = "user", resource_id: uuid.UUID | None = None) -> None:
    """Records a breach and, in enforce mode, commits that record and raises SodViolation.
    In warn mode the caller carries on (the record commits with its work)."""
    enforced = mode(session, tenant_id) == "enforce"
    if enforced:
        session.rollback()  # nothing of the refused action survives; only its record
    record(session, tenant_id, actor_user_id, violation, enforced=enforced, channel=channel,
           resource_type=resource_type, resource_id=resource_id)
    if enforced:
        session.commit()
        raise SodViolation(violation)


def presented_by(session: Session, tenant_id: uuid.UUID, source_item_id: uuid.UUID) -> uuid.UUID | None:
    """Who presented an item, from the audit log (the tamper-evident record of who created
    what, whatever the channel)."""
    from openclear.domain.audit_log_entry import AuditLogEntry

    return session.execute(
        select(AuditLogEntry.actor_user_id).where(
            AuditLogEntry.tenant_id == tenant_id, AuditLogEntry.resource_id == source_item_id,
            AuditLogEntry.action.in_(("paid_item.create", "ach_transaction.create", "check_image.upload")),
        ).order_by(AuditLogEntry.sequence).limit(1)
    ).scalar_one_or_none()


def check_decision(session: Session, tenant_id: uuid.UUID, ctx, exception, *, channel: str) -> None:
    """Before a pay/return decision: the person mustn't hold both sides of a rule, nor have
    presented the item themselves."""
    for rule in group_conflicts(ctx.permissions):
        if "exception:decide" in rule.right:
            breach(session, tenant_id, ctx.user_id, Violation(rule.key, f"{rule.title}: this person can also submit items"),
                   channel=channel, resource_type="exception_item", resource_id=exception.id)
    presenter = presented_by(session, tenant_id, exception.source_item_id)
    if presenter is not None and presenter == ctx.user_id:
        breach(session, tenant_id, ctx.user_id, Violation("presenter_decides", "Whoever presented an item can't decide its exception"),
               channel=channel, resource_type="exception_item", resource_id=exception.id)


def check_group(session: Session, tenant_id: uuid.UUID, actor_user_id: uuid.UUID | None, permissions, *, group_name: str) -> list[Rule]:
    """Before saving a security group: in enforce mode a conflicting group is refused. Returns
    the conflicts (for the warning shown in warn mode)."""
    conflicts = group_conflicts(permissions)
    for rule in conflicts:
        breach(session, tenant_id, actor_user_id, Violation(rule.key, f"{rule.title} (group {group_name})"), channel="web",
               resource_type="security_group")
    return conflicts


def set_mode(session: Session, tenant_id: uuid.UUID, new_mode: str) -> None:
    """Turning enforce on is refused while any group still breaks a rule (fix the groups first)."""
    from openclear.domain.security_group import SecurityGroup
    from openclear.domain.tenant import Tenant

    if new_mode not in ("warn", "enforce"):
        raise ValueError("Mode is warn or enforce.")
    if new_mode == "enforce":
        offending = [g.name for g in session.execute(select(SecurityGroup).where(SecurityGroup.tenant_id == tenant_id)).scalars()
                     if group_conflicts(g.permissions)]
        if offending:
            raise ValueError(f"These groups combine duties that must be separate: {', '.join(sorted(offending))}. Change them first.")
    session.get(Tenant, tenant_id).sod_mode = new_mode
    session.flush()
