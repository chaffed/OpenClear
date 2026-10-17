# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""OSCAL documents (CONTROLS_PLAN.md CT5; services/oscal_service.py), checked against NIST's
published OSCAL 1.2.1 JSON schemas (tests/fixtures/oscal/, from github.com/usnistgov/OSCAL)."""

import copy
import json
from datetime import datetime, time, timedelta, timezone
from functools import cache
from pathlib import Path

import jsonschema
from sqlalchemy import select

from openclear.compliance import controls, cuecs
from openclear.domain.control_result import ControlDeviation, ControlTestResult
from openclear.services import attestation_service, oscal_service
from tests.conftest import login_headers
from tests.test_compliance.test_attestations import DAY, _hourly_results

SCHEMAS = Path(__file__).resolve().parents[1] / "fixtures" / "oscal"


@cache
def _validator(model: str) -> jsonschema.Draft7Validator:
    """NIST's schema, with its one Unicode pattern (an XML NCName: \\p{L} letters, \\p{N} digits)
    written as Python's `re` can read it."""
    text = (SCHEMAS / f"oscal_{model}_schema.json").read_text()
    schema = json.loads(text.replace(r"\\p{L}", r"[^\\W\\d_]").replace(r"\\p{N}", r"\\d"))
    return jsonschema.Draft7Validator(schema, format_checker=jsonschema.Draft7Validator.FORMAT_CHECKER)


def _problems(model: str, document: dict) -> list[str]:
    return [f"{'/'.join(map(str, e.path))}: {e.message}" for e in _validator(model).iter_errors(document)]


def test_the_component_definition_is_valid_oscal():
    document = oscal_service.component_definition()
    assert _problems("component", document) == []
    definition = document["component-definition"]
    assert definition["metadata"]["oscal-version"] == "1.2.1"
    requirements = definition["components"][0]["control-implementations"][0]["implemented-requirements"]
    by_control = {r["control-id"]: r for r in requirements}
    assert len(by_control) == len(requirements)  # one per 800-53 control
    sod = by_control["ac-5"]
    assert {p["value"] for p in sod["props"] if p["name"] == "openclear-control"} == {"OC-AC-05", "OC-AC-06"}
    cuec = next(p for p in sod["props"] if p["name"] == "cuec" and p["value"] == "B-AC-5-01")
    assert cuec["class"] == "bank" and cuec["ns"] == oscal_service.NS and cuec["remarks"]
    assert {"role-id": "business-customer"} in sod["responsible-roles"]
    assert [p["value"] for p in sod["props"] if p["name"] == "csf-subcategory"] == ["PR.AA-05"]
    every = {p["value"] for r in requirements for p in r["props"]}
    assert every >= {c.id for c in controls.CONTROLS if not c.withdrawn} | {c.id for c in cuecs.CUECS if not c.withdrawn}
    assert oscal_service.component_definition()["component-definition"]["uuid"] == definition["uuid"]  # stable

    broken = copy.deepcopy(document)
    broken["component-definition"]["uuid"] = "not-a-uuid"
    del broken["component-definition"]["metadata"]["oscal-version"]
    assert len(_problems("component", broken)) == 2  # the check does find problems


def test_a_periods_assessment_results_are_valid_oscal(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="oscal-ar")
    attestation_service.add_mapping(db_session, tenant.id, None, "B-AC-5-01", control_ref="BANK-SOD-1", auto_accept=True)
    _hourly_results(db_session, tenant, "segregation_of_duties")
    first = db_session.execute(select(ControlTestResult).where(ControlTestResult.tenant_id == tenant.id)).scalars().first()
    opened = datetime.combine(DAY, time(3), tzinfo=timezone.utc)
    db_session.add(ControlDeviation(tenant_id=tenant.id, check_key="segregation_of_duties", status="FAIL", opened_at=opened,
                                    opened_result_id=first.id, closed_at=opened + timedelta(hours=1), cause="A rule changed",
                                    response="Put back"))
    attestation_service.prepare_automated(db_session, tenant.id, "monthly", DAY, DAY)
    db_session.commit()

    admin = login_headers(client, tenant.slug, users["admin"].email)
    period = {"start": DAY.isoformat(), "end": DAY.isoformat()}
    document = client.get("/api/v1/compliance/oscal/assessment-results", params=period, headers=admin).json()
    assert _problems("assessment-results", document) == []
    result = document["assessment-results"]["results"][0]
    observation = next(o for o in result["observations"] if {"name": "check", "ns": oscal_service.NS, "value": "segregation_of_duties"} in o["props"])
    assert "24 results" in observation["description"]
    finding = result["findings"][0]
    assert finding["target"] == {"type": "objective-id", "target-id": "ac-5_obj", "status": {"state": "not-satisfied", "reason": "fail"}}
    assert "Cause: A rule changed" in finding["description"]
    assert finding["related-observations"] == [{"observation-uuid": observation["uuid"]}]
    attested = [p["value"] for a in result["attestations"] for part in a["parts"] for p in part["props"] if p["name"] == "cuec"]
    assert attested == ["B-AC-5-01"]  # only the accepted one
    href = document["assessment-results"]["import-ap"]["href"]
    assert href == "#" + document["assessment-results"]["back-matter"]["resources"][0]["uuid"]

    exported = client.get("/api/v1/compliance/attestations/export", params={**period, "format": "oscal"}, headers=admin)
    assert exported.status_code == 200 and _problems("assessment-results", exported.json()) == []
    empty = client.get("/api/v1/compliance/oscal/assessment-results", params={"start": "2025-01-01", "end": "2025-01-31"}, headers=admin)
    assert _problems("assessment-results", empty.json()) == []  # a period with nothing in it is still valid


def test_the_component_definition_through_the_api(client, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="oscal-cd")
    viewer = login_headers(client, tenant.slug, users["viewer"].email)
    assert client.get("/api/v1/compliance/oscal/component-definition", headers=viewer).status_code == 403  # compliance:read
    admin = login_headers(client, tenant.slug, users["admin"].email)
    assert _problems("component", client.get("/api/v1/compliance/oscal/component-definition", headers=admin).json()) == []
