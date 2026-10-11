# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from openclear.config import get_settings
from openclear.db.tenancy import bind_session_to_tenant
from openclear.domain.tenant import Tenant
from openclear.domain.tenant_membership import TenantMembership
from openclear.domain.user import User
from openclear.repositories.tenant_membership_repo import TenantMembershipRepository
from openclear.services import ach_return_reason_service, security_group_service, user_service


@dataclass(frozen=True, slots=True)
class ProvisionedIdentity:
    tenant: Tenant
    admin_user: User
    membership: TenantMembership


def create_tenant_with_admin(
    session: Session,
    *,
    tenant_name: str,
    tenant_slug: str,
    admin_email: str,
    admin_password: str,
) -> ProvisionedIdentity:
    """First-run bootstrap: creates a tenant, seeds its default security groups, and
    creates an initial admin user with a membership in the seeded "Admin" group. Used by
    scripts/launcher.py when the database is empty — kept here (not inline in the
    launcher) so it stays testable and reusable if a future admin API wants the same
    'create a new tenant' operation."""
    # New banks start on the shared model and can't switch to a bank-only model until the
    # lock passes (FIX_PLAN.md Phase 5; services/tenant_ml_service.py).
    lock_days = get_settings().ml_new_bank_private_switch_lock_days
    tenant = Tenant(
        name=tenant_name,
        slug=tenant_slug,
        ml_private_switch_allowed_at=datetime.now(timezone.utc) + timedelta(days=lock_days),
    )
    session.add(tenant)
    session.flush()
    # Postgres RLS: everything below is this new bank's own rows.
    bind_session_to_tenant(session, tenant.id)

    groups = security_group_service.seed_default_security_groups(session, tenant.id)
    ach_return_reason_service.seed_default_ach_return_reasons(session, tenant.id)
    from openclear.services import check_return_reason_service

    check_return_reason_service.seed_default_check_return_reasons(session, tenant.id)

    admin_user = user_service.create_user_with_membership(
        session, tenant.id, email=admin_email, password=admin_password, security_group_id=groups["Admin"].id
    )
    membership = TenantMembershipRepository(session, tenant.id).list(user_id=admin_user.id)[0]

    return ProvisionedIdentity(tenant=tenant, admin_user=admin_user, membership=membership)
