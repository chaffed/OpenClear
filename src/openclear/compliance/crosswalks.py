# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Crosswalks from the SP 800-53 controls the inventories are filed under to other frameworks
(CONTROLS_PLAN.md CT6), derived from the frameworks' own published mappings rather than
written by hand:
- NIST CSF 2.0, from NIST's informative references (csf_data.py, made by
  scripts/import_crosswalks.py).
- SOC 2 (the 2017 Trust Services Criteria), from AICPA's mapping to SP 800-53 Rev. 5, which
  isn't redistributed: an installation reads its own copy (`soc2_mapping_path`, made by
  `scripts/import_crosswalks.py soc2`), and without one there's no SOC 2 column (None).

NIST maps CSF subcategories mostly to base controls. A subcategory that names a control also
covers its enhancements (AC-2 covers AC-2(1)), and one that names a whole family (IR) covers
every control in it; one that names only an enhancement doesn't cover its base control. AICPA's
mapping is read the same way: a criterion that lists a control covers its enhancements."""

import json
import os
from functools import cache

from openclear.compliance.csf_data import SOURCE as CSF_SOURCE, SOURCE_URL as CSF_SOURCE_URL, SUBCATEGORIES

CSF_VERSION = "NIST CSF 2.0"
CSF_FUNCTIONS = {"GV": "Govern", "ID": "Identify", "PR": "Protect", "DE": "Detect", "RS": "Respond", "RC": "Recover"}

__all__ = ["CSF_SOURCE", "CSF_SOURCE_URL", "CSF_VERSION", "CSF_FUNCTIONS", "SUBCATEGORIES", "csf_for", "csf_for_all", "csf_title",
           "soc2_mapping", "soc2_for", "soc2_for_all"]


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


# --- SOC 2, from the installation's own copy of AICPA's mapping ------------------------------


@cache
def _soc2_load(path: str, modified: float) -> dict:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data.get("criteria"), dict):
        raise ValueError(f"{path} isn't a SOC 2 mapping from scripts/import_crosswalks.py soc2")
    return data


def soc2_mapping() -> dict | None:
    """The configured mapping ({source, source_sha256, tsc_version, nist_version, criteria}), or
    None when there's none."""
    from openclear.config import get_settings

    path = get_settings().soc2_mapping_path.strip()
    if not path:
        return None
    return _soc2_load(path, os.path.getmtime(path))


def soc2_for(nist: str) -> tuple[str, ...] | None:
    """The Trust Services Criteria AICPA maps to this SP 800-53 control, or None without a mapping."""
    mapping = soc2_mapping()
    if mapping is None:
        return None
    return tuple(k for k, references in mapping["criteria"].items() if any(_covers(r, nist) for r in references))


def soc2_for_all(nist_ids) -> list[str] | None:
    mapping = soc2_mapping()
    if mapping is None:
        return None
    wanted = {k for nist in nist_ids for k in (soc2_for(nist) or ())}
    return [k for k in mapping["criteria"] if k in wanted]
