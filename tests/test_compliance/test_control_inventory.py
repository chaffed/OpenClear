# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""The OpenClear control inventory (CONTROLS_PLAN.md CT1): well formed, filed under real NIST
800-53 controls, every live check attached, and in step with the CUEC inventory."""

import re
from pathlib import Path

import pytest

from openclear.compliance import controls, cuecs

ROOT = Path(__file__).resolve().parents[2]


def test_ids_are_unique_and_in_known_families():
    ids = [c.id for c in controls.CONTROLS]
    assert len(ids) == len(set(ids))
    for c in controls.CONTROLS:
        assert re.fullmatch(r"OC-[A-Z]{2}-\d\d", c.id), c.id
        assert c.family in controls.FAMILIES, c.id


@pytest.mark.parametrize("c", controls.CONTROLS, ids=lambda c: c.id)
def test_each_control_is_complete(c):
    assert c.title and c.statement.endswith(".") and c.where
    assert c.type in controls.TYPES and c.nature in controls.NATURES
    assert c.nist and len(set(c.nist)) == len(c.nist)
    for nist in c.nist:
        assert cuecs.NIST_ID.match(nist) and nist in cuecs.NIST_TITLES, f"{c.id}: {nist}"
    for check in c.checks:
        assert check in controls.all_checks(), f"{c.id}: unknown check {check}"
    known = cuecs.by_id()
    for cuec in c.cuecs:
        assert cuec in known and not known[cuec].withdrawn, f"{c.id}: {cuec}"
    if c.nature == "configurable":
        assert c.cuecs, f"{c.id} depends on a bank's setting, so it relies on the CUEC that sets it"


def test_live_checks_are_the_registrys():
    from openclear.compliance import checks

    assert [c.key for c in checks.registry()] == list(checks.KEYS) == list(controls.LIVE_CHECKS)
    assert cuecs.EXISTING_CHECKS == set(controls.LIVE_CHECKS)
    assert not set(controls.LIVE_CHECKS) & set(cuecs.PLANNED_CHECKS)
    for check in checks.registry():
        assert check.kind in ("state", "population") and check.schedule in ("hourly", "nightly")
        assert check.scope in ("bank", "installation")


def test_every_live_check_tests_a_control():
    for check in controls.LIVE_CHECKS:
        assert controls.for_check(check), f"{check} isn't attached to any control"


def test_a_cuecs_check_is_on_a_control_that_relies_on_it():
    """A CUEC evidenced by a check belongs to a control carrying that check, so a failing check
    points at both the control and the bank's part in it."""
    for c in cuecs.CUECS:
        if c.check and not c.withdrawn:
            assert any(c.id in control.cuecs for control in controls.for_check(c.check)), \
                f"{c.id}: no control with check {c.check} relies on it"


def test_as_dict():
    data = controls.by_id()["OC-IA-02"].as_dict()
    assert data["family"] == "IA"
    assert data["nist"][0] == {"id": "IA-2(1)", "title": "Multi-factor Authentication to Privileged Accounts", "oscal_id": "ia-2.1"}
    assert {"key": "admin_mfa", "status": "live"} in data["checks"] and {"key": "staff_mfa", "status": "live"} in data["checks"]
    assert {"key": "software_currency", "status": "live"} in controls.by_id()["OC-CM-02"].as_dict()["checks"]
