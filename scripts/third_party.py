# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed
"""The third-party inventory (plans/THIRD_PARTY_PLAN.md TP-D1 to TP-D3): writes
docs/compliance/third-party.md, a page a person can read, and THIRD_PARTY_NOTICES.md, each shipped
component's license text with its copyright lines.

Both come from the same sources as the release SBOM (scripts/sbom.py): the hashed locks
(requirements/runtime.txt, requirements/dev.txt) and requirements/vendored.json, plus the SDKs' own
manifests. Licenses and license files are read from the installed packages, so run it where the
locks are installed exactly as the image installs them, on its base image:

    docker run --rm -v "$PWD:/src" -w /src python:3.13-slim sh -c \\
      "pip install -q --no-deps --require-hashes -r requirements/runtime.txt && \\
       pip install -q --no-deps --require-hashes -r requirements/dev.txt && \\
       python scripts/third_party.py"

It refuses to write anything if a package the image installs isn't installed at its locked
version. tests/test_compliance/test_third_party.py fails when the committed files are stale."""

import argparse
import importlib.metadata as md
import json
import re
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from defusedxml import ElementTree as ET  # the SDKs' .csproj files (dev lock)
from packaging.markers import Marker
from packaging.utils import canonicalize_name

ROOT = Path(__file__).resolve().parent.parent
PAGE = ROOT / "docs" / "compliance" / "third-party.md"
NOTICES = ROOT / "THIRD_PARTY_NOTICES.md"

# Where the image's Python runs: markers in the locks are evaluated for it, so the inventory says
# what the image really installs (Dockerfile: python:3.13-slim, CPython, Linux).
IMAGE_ENV = {
    "python_version": "3.13", "python_full_version": "3.13.0", "implementation_name": "cpython",
    "platform_python_implementation": "CPython", "sys_platform": "linux", "platform_system": "Linux",
    "os_name": "posix", "platform_machine": "x86_64", "extra": "",
}

# Trove classifiers to SPDX identifiers, for packages that state their license only that way.
_CLASSIFIERS = {
    "MIT License": "MIT", "BSD License": "BSD", "Apache Software License": "Apache-2.0", "ISC License (ISCL)": "ISC",
    "Mozilla Public License 2.0 (MPL 2.0)": "MPL-2.0", "Python Software Foundation License": "PSF-2.0",
    "GNU Lesser General Public License v2 or later (LGPLv2+)": "LGPL-2.0-or-later",
    "GNU Lesser General Public License v3 (LGPLv3)": "LGPL-3.0-only",
    "GNU Lesser General Public License v3 or later (LGPLv3+)": "LGPL-3.0-or-later",
    "GNU Library or Lesser General Public License (LGPL)": "LGPL", "The Unlicense (Unlicense)": "Unlicense",
    "Historical Permission Notice and Disclaimer (HPND)": "HPND", "Zope Public License": "ZPL-2.1",
    "GNU Affero General Public License v3 or later (AGPLv3+)": "AGPL-3.0-or-later",
    "GNU General Public License v2 or later (GPLv2+)": "GPL-2.0-or-later", "Mozilla Public License 1.1 (MPL 1.1)": "MPL-1.1",
}
# Short "License:" values that say the same as an SPDX identifier.
_STATED = {"MIT License": "MIT", "Apache 2.0": "Apache-2.0", "Apache License 2.0": "Apache-2.0", "Apache License, Version 2.0": "Apache-2.0",
           "BSD License": "BSD", "BSD 3-Clause": "BSD-3-Clause", "BSD-3": "BSD-3-Clause", "MPL 2.0": "MPL-2.0", "PSF": "PSF-2.0",
           "MIT license": "MIT", "PSFL": "PSF-2.0", "Modified BSD License": "BSD-3-Clause", "Apache Software License": "Apache-2.0"}
_LICENSE_FILE = re.compile(r"(^|/)(LICEN[CS]E|COPYING|NOTICE|AUTHORS)[^/]*$", re.IGNORECASE)


@dataclass
class Locked:
    name: str
    version: str
    marker: str | None
    via: list[str] = field(default_factory=list)

    @property
    def in_image(self) -> bool:
        return self.marker is None or Marker(self.marker).evaluate(IMAGE_ENV)

    @property
    def used_for(self) -> str:
        if any(v.startswith("openclear") for v in self.via):
            return "OpenClear"
        return "for " + ", ".join(sorted(self.via)) if self.via else ""


def read_lock(path: Path) -> list[Locked]:
    """Each pinned package with its environment marker and what it's there for (uv's "# via")."""
    found: list[Locked] = []
    current = None
    for line in path.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^([A-Za-z0-9_.\-]+)==([^\s;\\]+)\s*(?:;\s*(.*?))?\s*\\?$", line)
        if m:
            current = Locked(canonicalize_name(m.group(1)), m.group(2), m.group(3) or None)
            found.append(current)
            continue
        via = re.match(r"^\s+#\s+(?:via\s+)?([^\s#].*)$", line)
        if current is not None and via:
            text = via.group(1).strip()
            if text and text != "via":
                current.via.append(text.split(" (")[0] if not text.startswith("openclear") else "openclear")
    return found


def vendored() -> dict:
    return json.loads((ROOT / "requirements" / "vendored.json").read_text(encoding="utf-8"))


def license_of(dist: md.Distribution) -> str:
    meta = dist.metadata
    if meta.get("License-Expression"):
        return meta["License-Expression"].strip()
    stated = (meta.get("License") or "").strip()
    stated = _STATED.get(stated, stated)
    if stated and "\n" not in stated and len(stated) <= 60 and stated.upper() not in ("UNKNOWN", "DUAL LICENSE"):
        return stated
    classified = [c.split(" :: ")[-1] for c in meta.get_all("Classifier") or [] if c.startswith("License :: OSI Approved ::")
                  or c.startswith("License :: ")]
    classified = [_CLASSIFIERS.get(c, c) for c in classified if c not in ("OSI Approved",)]
    if classified:
        return " OR ".join(dict.fromkeys(classified))
    return stated.splitlines()[0][:60] if stated and stated.upper() != "UNKNOWN" else "Not stated; see its license file"


def license_texts(dist: md.Distribution) -> list[tuple[str, str]]:
    """(file name, text) for each license file the package ships."""
    names = list(dict.fromkeys(dist.metadata.get_all("License-File") or []))
    texts = []
    for name in names:
        for candidate in (f"licenses/{name}", name):
            text = dist.read_text(candidate)
            if text:
                texts.append((Path(name).name, text.strip()))
                break
    if texts:
        return texts
    for entry in dist.files or []:
        if ".dist-info/" in str(entry) and _LICENSE_FILE.search(str(entry)):
            text = Path(entry.locate()).read_text(encoding="utf-8", errors="replace").strip()
            if text:
                texts.append((Path(str(entry)).name, text))
    return texts


def installed(packages: list[Locked]) -> dict[str, md.Distribution]:
    """The installed distribution of each package the image installs, checked against the lock."""
    problems, found = [], {}
    for p in packages:
        if not p.in_image:
            continue
        try:
            dist = md.distribution(p.name)
        except md.PackageNotFoundError:
            problems.append(f"{p.name}=={p.version} isn't installed")
            continue
        if dist.version != p.version:
            problems.append(f"{p.name} is {dist.version}, the lock says {p.version}")
            continue
        found[p.name] = dist
    if problems:
        raise SystemExit("Install the locks first (see this script's docstring):\n  " + "\n  ".join(problems))
    return found


def _final_stage_apt(dockerfile: str) -> list[str]:
    final = dockerfile.rsplit("\nFROM ", 1)[-1]
    block = re.search(r"apt-get install -y --no-install-recommends \\\n(.*?)&&", final, re.S)
    return re.findall(r"[a-z0-9][a-z0-9.+\-]+", block.group(1)) if block else []


def sdks() -> list[tuple[str, str, str, str, str]]:
    """(SDK, package, version, license, shipped or development) from each SDK's manifest."""
    rows = []
    sdk_py = tomllib.loads((ROOT / "sdks" / "python" / "pyproject.toml").read_text(encoding="utf-8"))
    for dep in sdk_py["project"].get("dependencies", []):
        name, spec = re.match(r"([A-Za-z0-9_.\-]+)(.*)", dep).groups()
        try:
            lic = license_of(md.distribution(name))
        except md.PackageNotFoundError:
            lic = "See the package"
        rows.append(("Python", name, spec or "any", lic, "Runtime"))
    for sdk in ("typescript", "embed"):
        lock = json.loads((ROOT / "sdks" / sdk / "package-lock.json").read_text(encoding="utf-8"))
        seen = set()
        for path, entry in sorted(lock["packages"].items()):
            if not path or path.count("node_modules/") != 1:
                continue
            name = path.removeprefix("node_modules/")
            if name.startswith("@typescript/typescript-"):  # TypeScript's per-platform compilers: one row
                name = "@typescript/typescript-<platform>"
            if name in seen:
                continue
            seen.add(name)
            rows.append(("TypeScript" if sdk == "typescript" else "Embed (TypeScript)", name, entry.get("version", ""),
                         entry.get("license", "See the package"), "Development" if entry.get("dev") else "Runtime"))
    licenses = vendored()["sdk_packages"]
    for csproj in sorted((ROOT / "sdks" / "dotnet").rglob("*.csproj")):
        test_project = "tests" in csproj.parts
        for ref in ET.parse(csproj).getroot().iter("PackageReference"):
            name = ref.get("Include")
            rows.append((".NET", name, ref.get("Version", ""), licenses.get(name, "See the package"),
                         "Development" if test_project else "Runtime"))
    return rows


def _table(header: list[str], rows: list[list[str]]) -> str:
    out = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    out += ["| " + " | ".join(str(c).replace("|", "\\|") for c in row) + " |" for row in rows]
    return "\n".join(out)


def build() -> tuple[str, str]:
    runtime = read_lock(ROOT / "requirements" / "runtime.txt")
    dev = read_lock(ROOT / "requirements" / "dev.txt")
    runtime_names = {p.name for p in runtime}
    dev_only = [p for p in dev if p.name not in runtime_names]
    dists = installed([*runtime, *dev_only])
    assets = vendored()
    apt = _final_stage_apt((ROOT / "Dockerfile").read_text(encoding="utf-8"))
    listed = {s["name"] for s in assets["system"]}
    if not set(apt) <= listed:
        raise SystemExit(f"Add these to requirements/vendored.json's system list: {sorted(set(apt) - listed)}")

    in_image = [p for p in runtime if p.in_image]
    elsewhere = [p for p in runtime if not p.in_image]
    dev_rows = [p for p in dev_only if p.in_image]
    page = [
        "<!-- Generated by scripts/third_party.py from the locks and requirements/vendored.json; don't edit by hand. -->",
        "# Third-party components",
        "",
        "Everything OpenClear is built from that someone else wrote, with its version, license and what it's for. "
        "It comes from the same sources as each release's CycloneDX SBOM (`openclear-<version>.cdx.json` on the "
        "GitHub release), so the two agree. The license texts and copyright notices are in "
        "[THIRD_PARTY_NOTICES.md](../../THIRD_PARTY_NOTICES.md), which is also in the image beside `LICENSE`.",
        "",
        "OpenClear itself is licensed under the GNU AGPL 3.0 or later.",
        "",
        f"## In the image: Python packages ({len(in_image)})",
        "",
        "Installed from the hashed lock `requirements/runtime.txt` and nothing else.",
        "",
        _table(["Package", "Version", "License", "Used"],
               [[p.name, p.version, license_of(dists[p.name]), p.used_for] for p in in_image]),
        "",
    ]
    if elsewhere:
        page += [
            "The lock also names these for other Python versions or platforms; the image (Python 3.13 on Linux) doesn't "
            "install them: " + ", ".join(f"{p.name} {p.version}" for p in elsewhere) + ".",
            "",
        ]
    page += [
        "## In the web pages: vendored files",
        "",
        "Served from `src/openclear/static/`, checked against their SHA-256 in `requirements/vendored.json`.",
        "",
        _table(["Component", "Kind", "Version", "License", "Used for"],
               [[c["name"], c["kind"], c["version"], c["license"], c["used_for"]] for c in assets["components"]]),
        "",
        "## In the image: the operating system",
        "",
        "The base image and the system packages the image adds. Each Debian package's own copyright file is in "
        "the image at `/usr/share/doc/<package>/copyright`; each release's image SBOM lists every package in it.",
        "",
        _table(["Package", "License", "Used for"], [[s["name"], s["license"], s["used_for"]] for s in assets["system"]]),
        "",
        "## In the SDKs",
        "",
        "Each SDK is licensed Apache-2.0. Runtime packages are installed with an SDK; development ones only build and "
        "test it.",
        "",
        _table(["SDK", "Package", "Version", "License", "Kind"], [list(r) for r in sdks()]),
        "",
        f"## Used only for development and tests ({len(dev_rows)})",
        "",
        "In `requirements/dev.txt` and not in the image: the test suite, the browser checks, linting the docs and "
        "building the docs site.",
        "",
        _table(["Package", "Version", "License"], [[p.name, p.version, license_of(dists[p.name])] for p in dev_rows]),
        "",
    ]

    notices = [
        "<!-- Generated by scripts/third_party.py; don't edit by hand. -->",
        "# Third-party notices",
        "",
        "OpenClear is free software under the GNU Affero General Public License 3.0 or later (see `LICENSE`). It "
        "includes the components below, each under its own license, whose texts and copyright notices follow. The "
        "list of components, with what each is for, is in `docs/compliance/third-party.md`.",
        "",
        "The operating-system packages in the image carry their own copyright files in `/usr/share/doc/`.",
        "",
        "## Files vendored into the web pages",
        "",
    ]
    for c in assets["components"]:
        notices += [f"### {c['name']} {c['version']} ({c['license']})", ""]
        if c.get("copyright"):
            notices += [c["copyright"], ""]
        notices += ["```text", (ROOT / c["license_file"]).read_text(encoding="utf-8").strip(), "```", ""]
    notices += ["## Python packages in the image", ""]
    first_with: dict[str, str] = {}
    for p in in_image:
        dist = dists[p.name]
        notices += [f"### {p.name} {p.version} ({license_of(dist)})", ""]
        texts = license_texts(dist)
        if not texts and p.name in assets.get("python_license_files", {}):
            path = assets["python_license_files"][p.name]
            texts = [(f"{Path(path).name}, from the package's source (its wheel leaves it out)", (ROOT / path).read_text(encoding="utf-8").strip())]
        if not texts:
            notices += ["The package ships no license file; its metadata states the license above.", ""]
        for filename, text in texts:
            if text in first_with:
                notices += [f"`{filename}`: the same text as {first_with[text]}'s, above.", ""]
                continue
            first_with[text] = p.name
            notices += [f"`{filename}`:", "", "```text", text.replace("```", "'''"), "```", ""]
    return "\n".join(page).rstrip() + "\n", "\n".join(notices).rstrip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.parse_args()
    page, notices = build()
    PAGE.write_text(page, encoding="utf-8")
    NOTICES.write_text(notices, encoding="utf-8")
    print(f"wrote {PAGE.relative_to(ROOT)} and {NOTICES.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
