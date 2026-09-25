# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

from openclear.domain.tenant_membership import TenantMembership
from openclear.repositories.base import TenantScopedRepository


class TenantMembershipRepository(TenantScopedRepository[TenantMembership]):
    """In-tenant membership queries (list this tenant's users, get one membership by id).
    Deliberately does NOT cover "which tenants does this user belong to" — that one
    legitimately cross-tenant query lives in services/user_service.py, not here, since
    TenantScopedRepository always filters to a single tenant_id by design."""

    model = TenantMembership
