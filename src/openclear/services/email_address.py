# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed
"""What counts as an email address, wherever one is typed in: changing an address, inviting a
customer's person (REVIEW_FIXES_PLAN.md RF-D7)."""


def looks_like_email(email: str) -> bool:
    """name@domain.tld, with no spaces: checked by splitting rather than a regex, which took
    time in proportion to the square of the length (CODE_SCANNING_PLAN.md CS1)."""
    if not email or len(email) > 320 or any(ch.isspace() for ch in email) or email.count("@") != 1:
        return False
    local, domain = email.split("@")
    labels = domain.split(".")
    return bool(local) and len(labels) >= 2 and labels[0] != "" and labels[-1] != ""
