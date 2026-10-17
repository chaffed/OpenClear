# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""The SOC 2 crosswalk (CONTROLS_PLAN.md CT6): read from an installation's own copy of AICPA's
TSC to NIST 800-53 mapping, never shipped. The workbook here is made up, in AICPA's layout, and
pairs criteria with controls arbitrarily (board independence with account management): none of
AICPA's mapping is in this repository."""

import importlib.util
import json
from pathlib import Path

import openpyxl
import pytest

from openclear.compliance import controls, crosswalks
from openclear.config import get_settings
from openclear.services import oscal_service
from tests.conftest import login_headers
from tests.test_compliance.test_oscal import _problems
from tests.test_web.test_web_bulk_ach import _login

ROOT = Path(__file__).resolve().parents[2]


def _module(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


import_crosswalks = _module("import_crosswalks")
export_cuecs = _module("export_cuecs")


def _workbook(path: Path) -> Path:
    book = openpyxl.Workbook()
    overview = book.active
    overview.title = "Overview"
    for row in (("This document shows how ...", None), ("Version Info:", None), ("Trust Services Criteria", "2017 (test)"), ("NIST 800-53", "r5")):
        overview.append(row)
    sheet = book.create_sheet("NIST 800-53 as Points of Focus")
    for row in ((None, None, None), ("TRUST SERVICES CRITERIA", "FRAMEWORK REFERENCE", "FRAMEWORK SPECIFIC POINTS OF FOCUS"),
                ("CC1.2: a heading row", None, None), ("CC1.2", "AC-2a", "x"), ("CC1.2", "AC-3(2)", "x"), ("CC1.2", " IA-2(1) ", "x"),
                ("CC1.2", "None", "x"), ("CC2.3: a heading row", None, None), ("CC2.3", "SI-4", "x"), ("CC2.3", "AU-6b", "x")):
        sheet.append(row)
    book.save(path)
    return path


@pytest.fixture
def mapping(tmp_path, monkeypatch) -> Path:
    data = import_crosswalks.read_soc2(_workbook(tmp_path / "tsc.xlsx"))
    out = tmp_path / "soc2-mapping.json"
    out.write_text(json.dumps(data))
    monkeypatch.setattr(get_settings(), "soc2_mapping_path", str(out))
    return out


def test_the_workbook_is_read_as_ids_only(tmp_path):
    data = import_crosswalks.read_soc2(_workbook(tmp_path / "tsc.xlsx"))
    assert data["criteria"] == {"CC1.2": ["AC-2", "AC-3(2)", "IA-2(1)"], "CC2.3": ["AU-6", "SI-4"]}  # a statement part is its control
    assert (data["tsc_version"], data["nist_version"]) == ("2017 (test)", "r5") and len(data["source_sha256"]) == 64
    assert "x" not in json.dumps(data["criteria"])  # none of the workbook's text


def test_matching(mapping):
    assert crosswalks.soc2_for("AC-2(3)") == ("CC1.2",)  # a listed control covers its enhancements
    assert crosswalks.soc2_for("AC-3") == ()  # but a listed enhancement doesn't cover its base
    assert crosswalks.soc2_for("SI-4") == ("CC2.3",)
    assert crosswalks.soc2_for_all(["SI-4", "AC-2"]) == ["CC1.2", "CC2.3"]


def test_without_a_mapping_there_is_no_soc2_column(client, tenant_factory):
    assert crosswalks.soc2_for("AC-2") is None and controls.CONTROLS[0].as_dict()["soc2"] is None
    tenant, _account, users = tenant_factory.make(slug="soc2-none")
    rows = client.get("/api/v1/compliance/controls", headers=login_headers(client, tenant.slug, users["admin"].email)).json()["controls"]
    assert all(c["soc2"] is None for c in rows)


def test_with_a_mapping_it_reaches_the_api_the_page_and_oscal(mapping, client, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="soc2-on")
    admin = login_headers(client, tenant.slug, users["admin"].email)
    by_id = {c["id"]: c for c in client.get("/api/v1/compliance/controls", headers=admin).json()["controls"]}
    assert by_id["OC-AU-04"]["soc2"] == ["CC2.3"]  # SI-4
    assert by_id["OC-AC-05"]["soc2"] == []  # AC-5 isn't in this made-up mapping
    cuec = next(c for c in client.get("/api/v1/compliance/cuecs", headers=admin).json()["cuecs"] if c["id"] == "B-AC-2-01")
    assert cuec["soc2"] == ["CC1.2"]
    _login(client, tenant.slug, users["admin"].email)
    assert "SOC 2 CC1.2" in client.get("/ui/compliance/cuecs/B-AC-2-01").text

    document = oscal_service.component_definition()
    assert _problems("component", document) == []
    requirements = document["component-definition"]["components"][0]["control-implementations"][0]["implemented-requirements"]
    si4 = next(r for r in requirements if r["control-id"] == "si-4")
    assert [p["value"] for p in si4["props"] if p["name"] == "soc2-criterion"] == ["CC2.3"]


def test_the_published_inventories_never_carry_it(mapping):
    assert crosswalks.soc2_mapping() is not None
    for name, render in export_cuecs.OUTPUTS.items():
        assert "soc2" not in render().lower(), name
