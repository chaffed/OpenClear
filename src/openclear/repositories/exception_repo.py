# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import uuid

from openclear.domain.exception_item import ExceptionItem
from openclear.repositories.base import CustomerScopedRepository


class ExceptionRepository(CustomerScopedRepository[ExceptionItem]):
    model = ExceptionItem

    def get_for_update(self, id_: uuid.UUID, *, skip_locked: bool = False) -> ExceptionItem | None:
        """The item, locked for this transaction and read fresh (FULL_REVIEW_PLAN.md FR-D1): every
        change to an item's decision state takes this lock first, so two people (or a person and
        the cut-off) can't both act on what they each read. On Postgres a second caller waits, or
        with `skip_locked` gets None; SQLite serialises writers itself and ignores it."""
        stmt = (self.query().where(self.model.id == id_).with_for_update(skip_locked=skip_locked)
                .execution_options(populate_existing=True))
        return self.session.execute(stmt).scalar_one_or_none()
