# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Using something exactly once (FULL_REVIEW_PLAN.md FR2, R11): a reset link, an email-change
link, an invitation, an embed launch code. Reading `used_at` and then setting it lets two
requests in the same instant both see it unused; this sets it with a conditional UPDATE, and
only the request whose UPDATE changed the row may go on."""

from datetime import datetime, timezone

from sqlalchemy import update
from sqlalchemy.orm import Session


class AlreadyUsed(Exception):
    """Someone else used it a moment ago."""


def claim_once(session: Session, row, column: str = "used_at", when: datetime | None = None) -> datetime:
    """Marks `row.<column>` now, if it's still unset in the database; raises AlreadyUsed
    otherwise. Returns the time it set, which is also set on `row`."""
    when = when or datetime.now(timezone.utc)
    model = type(row)
    changed = session.execute(
        update(model).where(model.id == row.id, getattr(model, column).is_(None)).values({column: when})
        .execution_options(synchronize_session=False)
    ).rowcount
    if changed != 1:
        raise AlreadyUsed()
    setattr(row, column, when)
    return when
