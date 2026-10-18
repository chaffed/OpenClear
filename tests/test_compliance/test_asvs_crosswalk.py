# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed
"""docs/compliance/asvs.md (OWASP_PLAN.md OW-D3, OW-D4): generated, current, and every Met row has
evidence that exists."""

import importlib.util
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("asvs_crosswalk", ROOT / "scripts" / "asvs_crosswalk.py")
crosswalk = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(crosswalk)


def test_every_requirement_has_a_status_and_real_evidence():
    assert crosswalk.problems() == []


def test_the_page_is_current():
    result = subprocess.run([sys.executable, str(ROOT / "scripts" / "asvs_crosswalk.py"), "--check"], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_marked_tests_appear_as_evidence():
    marks = crosswalk.test_marks()
    assert "tests/test_auth/test_breached_passwords.py::test_the_bundled_list_knows_common_passwords" in marks["V6.2.4"]
