# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed
"""Release metrics see the release's own advisory (REVIEW_FIXES_PLAN.md RF-D8): they're written
at release step 1, while the advisory is still a draft, so drafts count too."""

import importlib.util
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("release_metrics", ROOT / "scripts" / "release_metrics.py")
release_metrics = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(release_metrics)

ADVISORIES = {
    "draft": [{"ghsa_id": "GHSA-dddd", "severity": "high", "summary": "Fixed in this one",
               "vulnerabilities": [{"patched_versions": "1.26.0"}]},
              {"ghsa_id": "GHSA-later", "severity": "low", "summary": "Not yet",
               "vulnerabilities": [{"patched_versions": "1.27.0"}]}],
    "published": [{"ghsa_id": "GHSA-pppp", "severity": None, "summary": "Out already",
                   "vulnerabilities": [{"patched_versions": "1.25.1, 1.26.0"}]}],
}


def _gh(monkeypatch, fail=False):
    def run(args, **_):
        if fail:
            raise subprocess.CalledProcessError(1, args)
        state = args[2].split("state=")[1].split("&")[0]
        return subprocess.CompletedProcess(args, 0, stdout=json.dumps(ADVISORIES[state]))
    monkeypatch.setattr(release_metrics.subprocess, "run", run)


def test_a_draft_advisory_for_this_version_counts(monkeypatch):
    _gh(monkeypatch)
    assert release_metrics.advisories("1.26.0") == ["GHSA-dddd (high): Fixed in this one", "GHSA-pppp (unrated): Out already"]
    assert release_metrics.advisories("1.24.0") == []


def test_github_unreachable_says_so(monkeypatch):
    _gh(monkeypatch, fail=True)
    assert release_metrics.advisories("1.26.0") is None
