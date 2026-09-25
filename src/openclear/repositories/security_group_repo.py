# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

from openclear.domain.security_group import SecurityGroup
from openclear.repositories.base import TenantScopedRepository


class SecurityGroupRepository(TenantScopedRepository[SecurityGroup]):
    model = SecurityGroup
