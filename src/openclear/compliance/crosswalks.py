# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Crosswalks from the SP 800-53 controls the inventories are filed under to other frameworks
(CONTROLS_PLAN.md CT6), derived from the frameworks' own published mappings rather than
written by hand:
- NIST CSF 2.0, from NIST's informative references (csf_data.py, made by
  scripts/import_crosswalks.py).

NIST maps CSF subcategories mostly to base controls. A subcategory that names a control also
covers its enhancements (AC-2 covers AC-2(1)), and one that names a whole family (IR) covers
every control in it; one that names only an enhancement doesn't cover its base control."""

from functools import cache

from openclear.compliance.csf_data import SOURCE as CSF_SOURCE, SOURCE_URL as CSF_SOURCE_URL, SUBCATEGORIES

CSF_VERSION = "NIST CSF 2.0"
CSF_FUNCTIONS = {"GV": "Govern", "ID": "Identify", "PR": "Protect", "DE": "Detect", "RS": "Respond", "RC": "Recover"}

__all__ = ["CSF_SOURCE", "CSF_SOURCE_URL", "CSF_VERSION", "CSF_FUNCTIONS", "SUBCATEGORIES", "csf_for", "csf_for_all", "csf_title"]


def _covers(reference: str, nist: str) -> bool:
    if reference == nist:
        return True
    if "-" not in reference:  # a whole family
        return nist.split("-", 1)[0] == reference
    return "(" not in reference and nist.split("(", 1)[0] == reference


@cache
def csf_for(nist: str) -> tuple[str, ...]:
    """The CSF 2.0 subcategories NIST maps to this SP 800-53 control or enhancement, in CSF order."""
    return tuple(key for key, (_text, references) in SUBCATEGORIES.items() if any(_covers(r, nist) for r in references))


def csf_for_all(nist_ids: tuple[str, ...] | list[str]) -> list[str]:
    """The subcategories for several controls together, each once, in CSF order."""
    wanted = {key for nist in nist_ids for key in csf_for(nist)}
    return [key for key in SUBCATEGORIES if key in wanted]


def csf_title(subcategory: str) -> str:
    return SUBCATEGORIES[subcategory][0]
