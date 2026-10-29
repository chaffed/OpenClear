# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed
"""The release SBOM (scripts/sbom.py, OWASP_PLAN.md OW-D11) lists what the image installs and the
vendored JavaScript and icons, and the vendored files are the ones the manifest names."""

import hashlib
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_vendored_files_match_their_manifest():
    manifest = json.loads((ROOT / "requirements" / "vendored.json").read_text())["components"]
    static = ROOT / "src" / "openclear" / "static"
    vendor_js = {f"js/vendor/{p.name}" for p in (static / "js" / "vendor").glob("*.js")}
    assert vendor_js | {"icons/sprite.svg"} == {c["file"] for c in manifest}
    for c in manifest:
        # Replacing a file (or re-running scripts/fetch_material_icons.py) means a new version and sha256 here.
        assert hashlib.sha256((static / c["file"]).read_bytes()).hexdigest() == c["sha256"], c["file"]


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
    assert {"mermaid", "svg-pan-zoom", "material symbols (outlined)"} <= names
    assert bom["metadata"]["component"]["name"] == "openclear"
