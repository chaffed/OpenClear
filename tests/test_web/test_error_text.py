# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""What a page says when something is refused (REVIEW_1_33_PLAN.md K2, RV-D9): a sentence written
for the person, never a library's exception text (CodeQL #176's class of finding)."""

import json
import re
from pathlib import Path

from openclear.errors import UserFacingError
from openclear.web.errors import GENERIC, shown

ROUTERS = Path(__file__).resolve().parents[2] / "src" / "openclear" / "web" / "routers"


def test_no_router_shows_an_exceptions_text_straight():
    offenders = []
    for path in sorted(ROUTERS.glob("*.py")):
        for n, line in enumerate(path.read_text().splitlines(), 1):
            if re.search(r"(error|flash|detail)=str\(exc\)|\{type\(exc\)\.__name__\}", line):
                offenders.append(f"{path.name}:{n}")
    assert offenders == [], "show refusals through web.errors.shown(): " + ", ".join(offenders)


def test_our_sentences_show_and_a_librarys_text_doesnt():
    try:
        raise UserFacingError("Type the bank's slug to confirm.")
    except UserFacingError as exc:
        assert shown(exc) == "Type the bank's slug to confirm."
    try:
        json.loads("{not json")  # raised inside the standard library
    except ValueError as exc:
        assert shown(exc) == GENERIC and "Expecting" not in shown(exc)

    def refuse():
        raise ValueError("Account not found")  # a plain ValueError, but ours

    try:
        refuse()
    except ValueError as exc:
        assert shown(exc) == GENERIC  # the frame is this test's, not openclear's: a library as far as the page knows
