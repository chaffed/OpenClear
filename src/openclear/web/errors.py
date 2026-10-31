# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""What a page says when a service refused something (REVIEW_1_33_PLAN.md K2, RV-D9)."""

import logging

from openclear.errors import UserFacingError

logger = logging.getLogger(__name__)

GENERIC = "That couldn't be done; nothing was saved. If it keeps happening, tell your administrator."


def shown(exc: BaseException) -> str:
    """The sentence a person sees for `exc`: its own text when it was written for them (a
    UserFacingError, or any error raised by OpenClear's own code, whose messages are), and a
    fixed sentence when it came from a library or a driver, whose text can carry internals
    (CodeQL #176's point). The detail is logged either way."""
    if isinstance(exc, UserFacingError) or _raised_by_openclear(exc):
        return str(exc) or GENERIC
    logger.warning("Refusal shown as a fixed sentence; the error was %s: %s", type(exc).__name__, exc)
    return GENERIC


def _raised_by_openclear(exc: BaseException) -> bool:
    tb = exc.__traceback__
    if tb is None:
        return False
    while tb.tb_next is not None:
        tb = tb.tb_next
    module = tb.tb_frame.f_globals.get("__name__", "")
    return module.startswith("openclear.")
