# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Turning an exception from a form submission into a message fit to show the user.

Several create/update routes catch any exception and re-render their form with the error.
They used to show `str(exc)`, which for a database constraint is the raw driver text
("(sqlite3.IntegrityError) UNIQUE constraint failed: ... [SQL: INSERT INTO ...]"):
confusing, and it exposes table and column names. This keeps the useful cases and hides
the rest."""

import logging

from sqlalchemy.exc import IntegrityError

logger = logging.getLogger(__name__)


def friendly_error(exc: Exception, *, action: str, duplicate: str | None = None) -> str:
    """`action` is what failed, e.g. "Could not create customer". A uniqueness conflict
    becomes `duplicate` (or a general "already exists"); a ValueError from a service is a
    deliberate, user-facing validation message (e.g. "Account not found") and is kept;
    anything else is logged and replaced with a generic message."""
    if isinstance(exc, IntegrityError):
        return duplicate or f"{action}: something with the same details already exists."
    if isinstance(exc, ValueError):
        return f"{action}: {exc}"
    logger.exception("%s", action)
    return f"{action}. Please check the details and try again."
