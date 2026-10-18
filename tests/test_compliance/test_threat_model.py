# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed
"""docs/security/threat-model.md is generated from threat_model/openclear.py (OWASP_PLAN.md
OW-D12): a change to the model that isn't re-rendered fails here."""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_threat_model_page_is_current():
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "render_threat_model.py"), "--check"], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr


def test_every_openclear_threat_has_a_mitigation_and_evidence():
    sys.path.insert(0, str(ROOT))
    from threat_model.openclear import OPENCLEAR_THREATS

    ids = [t.id for t in OPENCLEAR_THREATS]
    assert len(ids) == len(set(ids))
    for t in OPENCLEAR_THREATS:
        assert t.stride in "STRIDE" and t.mitigation and t.evidence and t.status, t.id
