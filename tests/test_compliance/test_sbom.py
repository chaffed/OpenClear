# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed
"""The release SBOM (scripts/sbom.py, OWASP_PLAN.md OW-D11) lists what the image installs and the
vendored JavaScript and icons, and the vendored files are the ones the manifest names."""

import hashlib
import importlib.util
import json
import re
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


def test_the_release_attaches_the_image_sbom_with_the_ci_images_syft():
    """plans/THIRD_PARTY_PLAN.md TP-D5: each release also carries openclear-<version>-image.cdx.json
    from Syft, the same Syft (pinned by digest) that scans the CI image."""
    release = (ROOT / ".github" / "workflows" / "release.yml").read_text()
    ci_image = (ROOT / ".github" / "workflows" / "ci-image.yml").read_text()
    pin = re.search(r"SYFT_IMAGE: (anchore/syft:\S+@sha256:[0-9a-f]{64})", release)
    assert pin and pin.group(1) in ci_image
    assert '-image.cdx.json"' in release and "gh release upload" in release.split("image-sbom:", 1)[1]


def test_the_lock_refresh_regenerates_the_third_party_inventory():
    refresh = (ROOT / ".github" / "workflows" / "lock-refresh.yml").read_text()
    assert refresh.index("scripts/lock.sh --upgrade") < refresh.index("scripts/third_party.sh") < refresh.index("create-pull-request")
