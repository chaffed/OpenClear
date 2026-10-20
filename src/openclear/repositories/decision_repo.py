# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

from openclear.domain.decision import Decision
from openclear.repositories.base import TenantScopedRepository


class DecisionRepository(TenantScopedRepository[Decision]):
    model = Decision

    def get_by_exception(self, exception_item_id) -> Decision | None:
        """An item's decision: there's at most one (decision.exception_item_id is unique)."""
        found = self.list(exception_item_id=exception_item_id)
        return found[0] if found else None
