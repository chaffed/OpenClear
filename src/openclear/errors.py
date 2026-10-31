# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""The one error type for sentences meant for people (REVIEW_1_33_PLAN.md K2, RV-D9).

A service that refuses something a person asked for raises a subclass of UserFacingError with a
sentence written for them; the web routers show that sentence (web/errors.py::shown). Any other
exception's text (a library's, a database driver's) is never shown: the person gets a fixed
sentence and the log gets the detail. Kept free of imports so every layer can use it."""


class UserFacingError(ValueError):
    """Fit to show to the person who asked."""
