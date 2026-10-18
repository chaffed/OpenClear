# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed
"""The release SBOM (scripts/sbom.py, OWASP_PLAN.md OW-D11) lists what the image installs and the
vendored JavaScript, and the vendored files are the ones the manifest names."""

import hashlib
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_vendored_javascript_matches_its_manifest():
    manifest = json.loads((ROOT / "requirements" / "vendored-js.json").read_text())["components"]
    vendor = ROOT / "src" / "openclear" / "static" / "js" / "vendor"
    assert {c["file"] for c in manifest} == {p.name for p in vendor.glob("*.js")}
    for c in manifest:
        assert hashlib.sha256((vendor / c["file"]).read_bytes()).hexdigest() == c["sha256"], c["file"]


def test_sbom_lists_every_locked_package_and_the_vendored_javascript():
    spec = importlib.util.spec_from_file_location("sbom", ROOT / "scripts" / "sbom.py")
    sbom = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sbom)
    bom = sbom.build()
    names = {c["name"].lower() for c in bom["components"]}
    locked = {
        line.split("==")[0].lower() for line in (ROOT / "requirements" / "runtime.txt").read_text().splitlines()
        if "==" in line and not line.startswith((" ", "#"))
    }
    assert locked <= names
    assert {"mermaid", "svg-pan-zoom"} <= names
    assert bom["metadata"]["component"]["name"] == "openclear"
