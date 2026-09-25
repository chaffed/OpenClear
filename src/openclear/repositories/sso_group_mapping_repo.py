# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

from openclear.domain.sso_connection import SsoGroupMapping
from openclear.repositories.base import TenantScopedRepository


class SsoGroupMappingRepository(TenantScopedRepository[SsoGroupMapping]):
    model = SsoGroupMapping
