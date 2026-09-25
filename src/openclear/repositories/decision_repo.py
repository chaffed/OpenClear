# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

from openclear.domain.decision import Decision
from openclear.repositories.base import TenantScopedRepository


class DecisionRepository(TenantScopedRepository[Decision]):
    model = Decision
