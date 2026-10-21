# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from openclear.auth.permissions import DEFAULT_SECURITY_GROUPS, PERMISSION_CATALOG
from openclear.domain.security_group import NO_ACCESS, NO_ACCESS_NAME
from openclear.domain.security_group import SecurityGroup
from openclear.repositories.security_group_repo import SecurityGroupRepository


@dataclass(frozen=True, slots=True)
class SecurityGroupInput:
    name: str
    permissions: list[str]
    # Whether customers' own administrators may give it to their people
    # (CUSTOMER_ADMIN_PLAN.md). None leaves it as it is.
    offered_to_customers: bool | None = None


def _clean_permissions(permissions: list[str]) -> list[str]:
    # Silently drop anything not in the catalog rather than erroring — a checkbox list
    # only ever submits known keys, but this keeps a hand-crafted/replayed form post from
    # smuggling an arbitrary string into a group's permission set.
    return [p for p in permissions if p in PERMISSION_CATALOG]


def seed_default_security_groups(session: Session, tenant_id: uuid.UUID) -> dict[str, SecurityGroup]:
    """Called once when a tenant is created (provisioning_service.py, tests'
    TenantFactory) so every tenant starts with the same default groups
    (Admin/Preparer/Approver/Viewer/Bookkeeper) — fully editable from there. Kept in one
    place so the two callers can never seed different defaults."""
    repo = SecurityGroupRepository(session, tenant_id)
    groups: dict[str, SecurityGroup] = {}
    for name, permissions in DEFAULT_SECURITY_GROUPS.items():
        group = SecurityGroup(name=name, permissions=list(permissions))
        repo.add(group)
        groups[name] = group
    # Built in, for people a bank invites (UI_PLAN.md UI-D14): signed in, nothing else.
    groups[NO_ACCESS_NAME] = SecurityGroup(name=NO_ACCESS_NAME, permissions=[], system_role=NO_ACCESS)
    repo.add(groups[NO_ACCESS_NAME])
    session.flush()
    return groups


def no_access_group(session: Session, tenant_id: uuid.UUID) -> SecurityGroup:
    """The bank's built-in "No access yet" group; created if a bank somehow lacks it."""
    from sqlalchemy import select

    group = session.execute(select(SecurityGroup).where(SecurityGroup.tenant_id == tenant_id,
                                                        SecurityGroup.system_role == NO_ACCESS)).scalar_one_or_none()
    if group is None:
        group = SecurityGroup(tenant_id=tenant_id, name=NO_ACCESS_NAME, permissions=[], system_role=NO_ACCESS)
        session.add(group)
        session.flush()
    return group


def list_security_groups(
    session: Session,
    tenant_id: uuid.UUID,
    *,
    limit: int | None = None,
    offset: int | None = None,
    order_by: Any = None,
) -> list[SecurityGroup]:
    return SecurityGroupRepository(session, tenant_id).list(limit=limit, offset=offset, order_by=order_by)


def get_security_group(session: Session, tenant_id: uuid.UUID, group_id: uuid.UUID) -> SecurityGroup | None:
    return SecurityGroupRepository(session, tenant_id).get(group_id)


def get_security_group_by_name(session: Session, tenant_id: uuid.UUID, name: str) -> SecurityGroup | None:
    matches = SecurityGroupRepository(session, tenant_id).list(name=name)
    return matches[0] if matches else None


def create_security_group(session: Session, tenant_id: uuid.UUID, data: SecurityGroupInput) -> SecurityGroup:
    repo = SecurityGroupRepository(session, tenant_id)
    group = SecurityGroup(name=data.name, permissions=_clean_permissions(data.permissions),
                          offered_to_customers=bool(data.offered_to_customers))
    repo.add(group)
    session.flush()
    return group


def update_security_group(
    session: Session, tenant_id: uuid.UUID, group_id: uuid.UUID, data: SecurityGroupInput
) -> SecurityGroup | None:
    repo = SecurityGroupRepository(session, tenant_id)
    group = repo.get(group_id)
    if group is None:
        return None
    if group.system_role:
        raise ValueError(f"{group.name} is built in and can't be changed: it holds people until someone gives them a role.")
    group.name = data.name
    group.permissions = _clean_permissions(data.permissions)
    if data.offered_to_customers is not None:
        group.offered_to_customers = data.offered_to_customers
    session.flush()
    return group
