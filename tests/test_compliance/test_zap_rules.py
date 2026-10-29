# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed
"""zap-rules.tsv waivers (scripts/zap_scan.py): a waiver limited to some URLs covers an alert only
where ZAP found it on those URLs, so accepting a false positive on one page never hides the same
alert on another."""

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _zap():
    spec = importlib.util.spec_from_file_location("zap_scan", ROOT / "scripts" / "zap_scan.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _alert(*uris):
    return {"pluginid": "10099", "name": "Source Code Disclosure - SQL", "instances": [{"uri": u} for u in uris]}


def test_a_url_limited_waiver_covers_only_its_page():
    zap = _zap()
    accepted = zap.waived()
    assert zap._waiver(accepted, _alert("http://openclear-zap:8000/ui/license/third-party"))
    assert zap._waiver(accepted, _alert("http://openclear-zap:8000/ui/license/third-party", "http://openclear-zap:8000/ui/exceptions")) is None
    assert zap._waiver(accepted, _alert("http://openclear-zap:8000/ui/admin/sso")) is None


def test_every_waiver_has_a_reason():
    for (rule, _name), (reason, _urls) in _zap().waived().items():
        assert rule.isdigit() and reason
