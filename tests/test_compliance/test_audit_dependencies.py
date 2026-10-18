# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed
"""The dependency gate's waivers (scripts/audit_dependencies.py, OWASP_PLAN.md OW-D8): a waiver
needs a reason and an expiry, and an expired one fails the run."""

import datetime as dt
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("audit_dependencies", ROOT / "scripts" / "audit_dependencies.py")
audit_dependencies = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(audit_dependencies)


def _waivers(tmp_path, monkeypatch, text):
    path = tmp_path / "waivers.toml"
    path.write_text(text)
    monkeypatch.setattr(audit_dependencies, "WAIVERS", path)
    return audit_dependencies.load_waivers(dt.date(2026, 10, 17))


def test_a_current_waiver_applies(tmp_path, monkeypatch):
    waivers, problems = _waivers(tmp_path, monkeypatch, '[[waiver]]\nid = "PYSEC-1"\nreason = "not reachable"\nexpires = 2026-11-01\n')
    assert "PYSEC-1" in waivers and problems == []


def test_an_expired_waiver_fails(tmp_path, monkeypatch):
    waivers, problems = _waivers(tmp_path, monkeypatch, '[[waiver]]\nid = "PYSEC-1"\nreason = "x"\nexpires = 2026-10-01\n')
    assert waivers == {} and "expired" in problems[0]


def test_a_waiver_without_a_reason_or_date_fails(tmp_path, monkeypatch):
    waivers, problems = _waivers(tmp_path, monkeypatch, '[[waiver]]\nid = "PYSEC-1"\nexpires = "soon"\n')
    assert waivers == {} and "needs id, reason and expires" in problems[0]


def test_the_repository_waivers_are_valid():
    _waivers_now, problems = audit_dependencies.load_waivers(dt.date.today())
    assert problems == []
