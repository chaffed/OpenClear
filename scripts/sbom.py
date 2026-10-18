# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed
"""A CycloneDX SBOM for a release (OWASP_PLAN.md OW-D11): every Python package the image
installs (requirements/runtime.txt, with hashes) plus the JavaScript vendored into
static/js/vendor (requirements/vendored-js.json).

    python scripts/sbom.py -o openclear-<version>.cdx.json
"""

import argparse
import json
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def build() -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "python.cdx.json"
        subprocess.run(
            [sys.executable, "-m", "cyclonedx_py", "requirements", str(ROOT / "requirements" / "runtime.txt"),
             "--output-format", "JSON", "--output-file", str(out)],
            check=True, capture_output=True,
        )
        bom = json.loads(out.read_text(encoding="utf-8"))
    version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    bom.setdefault("metadata", {})["component"] = {
        "type": "application", "bom-ref": f"pkg:pypi/openclear@{version}", "name": "openclear", "version": version,
        "purl": f"pkg:pypi/openclear@{version}", "licenses": [{"license": {"id": "AGPL-3.0-or-later"}}],
    }
    vendored = json.loads((ROOT / "requirements" / "vendored-js.json").read_text(encoding="utf-8"))["components"]
    bom.setdefault("components", []).extend(
        {
            "type": "library", "bom-ref": c["purl"], "name": c["name"], "version": c["version"], "purl": c["purl"],
            "licenses": [{"license": {"id": c["license"]}}],
            "hashes": [{"alg": "SHA-256", "content": c["sha256"]}],
            "properties": [{"name": "openclear:vendored-file", "value": f"src/openclear/static/js/vendor/{c['file']}"}],
        }
        for c in vendored
    )
    return bom


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("-o", "--output", required=True)
    args = parser.parse_args()
    bom = build()
    Path(args.output).write_text(json.dumps(bom, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {args.output}: {len(bom['components'])} components")
    return 0


if __name__ == "__main__":
    sys.exit(main())
