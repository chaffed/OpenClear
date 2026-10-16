# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""The CUEC inventory (CONTROLS_PLAN.md CT0): well formed, filed under real NIST 800-53
controls, pointing at checks that exist or are planned, and published as generated."""

import importlib.util
import re
from pathlib import Path

import pytest

from openclear.compliance import cuecs

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("export_cuecs", ROOT / "scripts" / "export_cuecs.py")
export_cuecs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(export_cuecs)


def test_ids_are_unique_and_follow_party_and_nist():
    ids = [c.id for c in cuecs.CUECS]
    assert len(ids) == len(set(ids))
    for c in cuecs.CUECS:
        assert re.fullmatch(rf"{re.escape(cuecs.PARTY_LETTERS[c.party])}-{re.escape(c.nist)}-\d\d", c.id), c.id


@pytest.mark.parametrize("c", cuecs.CUECS, ids=lambda c: c.id)
def test_each_cuec_is_complete(c):
    assert c.party in cuecs.PARTIES
    assert c.evidence in cuecs.EVIDENCE
    for nist in (c.nist, *c.related):
        assert cuecs.NIST_ID.match(nist), nist
        assert nist in cuecs.NIST_TITLES, f"{c.id}: {nist} has no title in NIST_TITLES"
    assert c.nist not in c.related
    assert c.title and c.statement.endswith(".") and c.complements.endswith(".") and c.frequency and c.where
    if c.evidence == "automated":
        assert c.check, f"{c.id} is automated but names no check"
    if c.check:
        assert c.check in cuecs.EXISTING_CHECKS | set(cuecs.PLANNED_CHECKS), c.check


def test_existing_checks_are_in_the_registry():
    from openclear.compliance import checks

    assert cuecs.EXISTING_CHECKS == {c.key for c in checks.registry()}
    assert not cuecs.EXISTING_CHECKS & set(cuecs.PLANNED_CHECKS)


def test_every_planned_check_is_used():
    used = {c.check for c in cuecs.CUECS}
    assert set(cuecs.PLANNED_CHECKS) <= used, set(cuecs.PLANNED_CHECKS) - used


def test_oscal_ids():
    assert cuecs.oscal_id("AC-2") == "ac-2"
    assert cuecs.oscal_id("IA-2(1)") == "ia-2.1"
    assert cuecs.oscal_id("SC-28(1)") == "sc-28.1"


@pytest.mark.parametrize("name", sorted(export_cuecs.OUTPUTS))
def test_published_copies_are_current(name):
    committed = (ROOT / "docs" / "compliance" / name).read_text(encoding="utf-8")
    assert committed == export_cuecs.OUTPUTS[name](), f"docs/compliance/{name} is out of date: python scripts/export_cuecs.py"
