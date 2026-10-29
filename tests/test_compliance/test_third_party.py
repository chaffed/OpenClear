# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed
"""The third-party inventory and notices (scripts/third_party.py; plans/THIRD_PARTY_PLAN.md TP1)
name exactly what the locks, the vendored files, the Dockerfile and the SDKs' manifests do, at
the same versions. Licenses come from the installed packages, so the files are regenerated only
where the locks are installed exactly (see the script); here, wherever that isn't so, the test
still catches a package added, removed or bumped without regenerating them."""

import importlib.util
import re
from pathlib import Path

import pytest
from defusedxml import ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]


def _script():
    spec = importlib.util.spec_from_file_location("third_party", ROOT / "scripts" / "third_party.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _rows(markdown: str, heading: str) -> list[list[str]]:
    """The cells of the table under a heading that starts with `heading`."""
    section = markdown.split(f"\n## {heading}", 1)[1].split("\n## ", 1)[0]
    lines = [line for line in section.splitlines() if line.startswith("| ")][1:]  # without the header row
    return [[cell.strip() for cell in line.strip("|").split(" | ")] for line in lines]


def test_the_page_names_every_locked_package_at_its_version():
    tp = _script()
    page = (ROOT / "docs" / "compliance" / "third-party.md").read_text()
    runtime = tp.read_lock(ROOT / "requirements" / "runtime.txt")
    in_image = {(p.name, p.version) for p in runtime if p.in_image}
    assert {(r[0], r[1]) for r in _rows(page, "In the image: Python packages")} == in_image, \
        "Stale: regenerate with scripts/third_party.py (its docstring says how)"
    for p in runtime:
        if not p.in_image:
            assert f"{p.name} {p.version}" in page, p.name
    names = {p.name for p in runtime}
    dev_only = {(p.name, p.version) for p in tp.read_lock(ROOT / "requirements" / "dev.txt") if p.name not in names and p.in_image}
    assert {(r[0], r[1]) for r in _rows(page, "Used only for development")} == dev_only


def test_the_page_names_the_vendored_files_the_system_packages_and_the_sdks():
    tp = _script()
    page = (ROOT / "docs" / "compliance" / "third-party.md").read_text()
    assets = tp.vendored()
    assert [(r[0], r[2]) for r in _rows(page, "In the web pages")] == [(c["name"], c["version"]) for c in assets["components"]]
    assert [r[0] for r in _rows(page, "In the image: the operating system")] == [s["name"] for s in assets["system"]]
    assert [tuple(r) for r in _rows(page, "In the SDKs")] == [tuple(r) for r in tp.sdks()]


def test_the_notices_cover_everything_shipped():
    tp = _script()
    notices = (ROOT / "THIRD_PARTY_NOTICES.md").read_text()
    headings = set(re.findall(r"^### (\S+ \S+) \(", notices, re.M))
    for p in tp.read_lock(ROOT / "requirements" / "runtime.txt"):
        if p.in_image:
            assert f"{p.name} {p.version}" in headings, f"{p.name}: regenerate with scripts/third_party.py"
    for c in tp.vendored()["components"]:
        assert f"### {c['name']} {c['version']} (" in notices
        assert (ROOT / c["license_file"]).exists()


def test_the_dockerfiles_system_packages_and_the_dotnet_references_are_listed():
    tp = _script()
    assets = tp.vendored()
    apt = tp._final_stage_apt((ROOT / "Dockerfile").read_text())
    assert apt and set(apt) == {s["name"] for s in assets["system"]} - {"python:3.13-slim"}
    assert 'FROM python:3.13-slim' in (ROOT / "Dockerfile").read_text()
    for csproj in (ROOT / "sdks" / "dotnet").rglob("*.csproj"):
        for ref in ET.parse(csproj).getroot().iter("PackageReference"):
            assert ref.get("Include") in assets["sdk_packages"], ref.get("Include")
    for path in assets.get("python_license_files", {}).values():
        assert (ROOT / path).exists()


def test_regenerating_gives_the_committed_files_where_the_locks_are_installed():
    tp = _script()
    try:
        page, notices = tp.build()
    except SystemExit:
        pytest.skip("The locks aren't installed exactly here (CI installs only the development lock)")
    assert page == (ROOT / "docs" / "compliance" / "third-party.md").read_text()
    assert notices == (ROOT / "THIRD_PARTY_NOTICES.md").read_text()

