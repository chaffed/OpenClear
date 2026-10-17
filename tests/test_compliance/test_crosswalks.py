# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""The NIST CSF 2.0 crosswalk (CONTROLS_PLAN.md CT6): made from NIST's own mapping, applied to
both inventories, in the API, and the FFIEC page's control ids."""

import importlib.util
import re
from pathlib import Path

from openclear.compliance import controls, crosswalks, csf_data, cuecs
from tests.conftest import login_headers
from tests.test_compliance.test_compliance_api import _customer_person

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("import_crosswalks", ROOT / "scripts" / "import_crosswalks.py")
import_crosswalks = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(import_crosswalks)


def test_the_crosswalk_is_nists_own_mapping():
    """csf_data.py is exactly what the import script makes from the NIST download kept with the tests."""
    committed = (ROOT / "src" / "openclear" / "compliance" / "csf_data.py").read_text(encoding="utf-8")
    assert committed == import_crosswalks.render(import_crosswalks.SOURCE), "csf_data.py is out of date: python scripts/import_crosswalks.py"


def test_the_subcategories():
    assert len(csf_data.SUBCATEGORIES) == 106  # CSF 2.0's, without the withdrawn ones
    assert {key[:2] for key in csf_data.SUBCATEGORIES} == set(crosswalks.CSF_FUNCTIONS)
    for key, (text, references) in csf_data.SUBCATEGORIES.items():
        assert re.match(r"^(GV|ID|PR|DE|RS|RC)\.[A-Z]{2}-\d{2}$", key) and text and references, key
        for reference in references:
            assert cuecs.NIST_ID.match(reference) or re.match(r"^[A-Z]{2}$", reference), (key, reference)
    assert crosswalks.csf_title("PR.AA-05").startswith("Access permissions, entitlements, and authorizations are defined")


def test_how_a_control_is_matched():
    assert crosswalks.csf_for("AC-5") == ("PR.AA-05",)
    assert crosswalks.csf_for("IA-2(1)") == crosswalks.csf_for("IA-2")  # an enhancement has its base control's
    assert "GV.OC-04" in crosswalks.csf_for("CP-2(8)") and "GV.OC-04" not in crosswalks.csf_for("CP-2")  # but not the reverse
    family_only = next(key for key, (_text, refs) in csf_data.SUBCATEGORIES.items() if "IR" in refs and "IR-4" not in refs)
    assert family_only in crosswalks.csf_for("IR-4")  # a whole family covers each control in it
    assert crosswalks.csf_for("AC-21") == ()  # NIST maps no subcategory to it
    assert crosswalks.csf_for_all(["AC-5", "AC-3"]) == [k for k in csf_data.SUBCATEGORIES if k in {*crosswalks.csf_for("AC-3"), "PR.AA-05"}]


def test_both_inventories_carry_it():
    sod = next(c for c in controls.CONTROLS if c.id == "OC-AC-05").as_dict()
    assert sod["csf"] == [{"id": "PR.AA-05", "title": crosswalks.csf_title("PR.AA-05")}]
    for cuec in cuecs.CUECS:
        assert [k["id"] for k in cuec.as_dict()["csf"]] == list(crosswalks.csf_for(cuec.nist))
    assert sum(bool(c.as_dict()["csf"]) for c in controls.CONTROLS) == len(controls.CONTROLS) - 1  # all but OC-AC-11 (AC-21)


def test_the_api_gives_it(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="ct6-csf")
    admin = login_headers(client, tenant.slug, users["admin"].email)
    by_id = {c["id"]: c for c in client.get("/api/v1/compliance/controls", headers=admin).json()["controls"]}
    assert [k["id"] for k in by_id["OC-IA-02"]["csf"]] == crosswalks.csf_for_all(["IA-2(1)", "IA-2(2)", "IA-2(8)"])
    customer = login_headers(client, tenant.slug, _customer_person(db_session, tenant))
    rows = client.get("/api/v1/compliance/cuecs", headers=customer).json()["cuecs"]
    assert rows and all(r["csf"] == [{"id": k, "title": crosswalks.csf_title(k)} for k in crosswalks.csf_for(r["nist"])] for r in rows)


def test_the_ffiec_page_cites_the_inventorys_nist_ids():
    page = (ROOT / "docs" / "compliance" / "ffiec.md").read_text(encoding="utf-8")
    cited = re.findall(r"(OC-[A-Z]{2}-\d{2}): ([A-Z0-9(), -]+?)(?=<br>| \|)", page)
    by_id = {c.id: c for c in controls.CONTROLS}
    assert len(cited) >= 25
    for control_id, nist in cited:
        assert nist.split(", ") == list(by_id[control_id].nist), control_id


def test_the_cuec_page_shows_it(client, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="ct6-page")
    client.get("/ui/login")
    client.post("/ui/login", data={"tenant_slug": tenant.slug, "email": users["admin"].email, "password": "test-password-123",
                                   "csrf_token": client.cookies.get("csrf_token"), "next": "/ui/"})
    page = client.get("/ui/compliance/cuecs/B-AC-5-01")
    assert page.status_code == 200 and "CSF 2.0" in page.text and ">PR.AA-05</abbr>" in page.text
