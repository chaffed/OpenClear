# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

from openclear.domain.sso_connection import SsoConnection
from openclear.repositories.base import TenantScopedRepository


class SsoConnectionRepository(TenantScopedRepository[SsoConnection]):
    """Deliberately a plain TenantScopedRepository, not CustomerScopedRepository:
    callers need an EXACT match on customer_id (None = bank-wide only, a real value =
    that one customer only), not CustomerScopedRepository's "None means don't filter at
    all" semantics — see services/sso_service.py, which builds its own
    `customer_id == ...` filter (correctly rendering `IS NULL` for None, same as
    auth/deps.py already relies on elsewhere)."""

    model = SsoConnection
