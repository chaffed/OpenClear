# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""The GRC API (CONTROLS_PLAN.md CT5): results and deviations, mappings, evidence, attestations
recorded, accepted and pulled, their exports, scope and permissions."""

import csv
import io
import json
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import select

from openclear.domain.audit_log_entry import AuditLogEntry
from openclear.domain.control_result import ControlDeviation, ControlTestResult
from openclear.domain.security_event import SecurityEvent
from openclear.services import api_client_service, attestation_service, evidence_service, security_group_service
from tests.conftest import login_headers
from tests.test_compliance.test_attestations import DAY, _hourly_results
from tests.test_compliance.test_compliance_api import _customer_person

PERIOD = {"start": DAY.isoformat(), "end": DAY.isoformat()}


def _admin_client(db_session, tenant, client, *, read_only: bool) -> dict:
    admin = next(g for g in security_group_service.list_security_groups(db_session, tenant.id) if g.name == "Admin")
    issued = api_client_service.create_client(db_session, tenant.id, name=f"GRC {read_only}", security_group_id=admin.id, read_only=read_only)
    db_session.commit()
    token = client.post("/api/v1/oauth/token", data={"grant_type": "client_credentials", "client_id": issued.client.client_id,
                                                     "client_secret": issued.secret}).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def _deviation(db_session, tenant, *, hours: int, cause=None):
    result = db_session.execute(select(ControlTestResult).where(ControlTestResult.tenant_id == tenant.id)).scalars().first()
    opened = datetime.combine(DAY, time(1), tzinfo=timezone.utc)
    deviation = ControlDeviation(tenant_id=tenant.id, check_key="segregation_of_duties", status="FAIL", opened_at=opened,
                                 opened_result_id=result.id, closed_at=opened + timedelta(hours=hours), cause=cause)
    db_session.add(deviation)
    db_session.commit()
    return deviation


def test_results_and_deviations(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="grc-results")
    _hourly_results(db_session, tenant, "segregation_of_duties")
    _deviation(db_session, tenant, hours=2, cause="A rule was changed")
    admin = login_headers(client, tenant.slug, users["admin"].email)

    page = client.get("/api/v1/compliance/controls/OC-AC-05/results", params={**PERIOD, "limit": 10}, headers=admin)
    assert page.status_code == 200 and len(page.json()) == 10 and page.headers["X-Total-Count"] == "24"
    assert {r["check_key"] for r in page.json()} == {"segregation_of_duties"} and page.json()[0]["started_by"] == "schedule"
    # Changed since: the next page follows the last row, not an offset.
    last = page.json()[-1]
    rest = client.get("/api/v1/compliance/controls/OC-AC-05/results",
                      params={"updated_since": last["recorded_at"], "after_id": last["id"], "limit": 100}, headers=admin).json()
    assert len(rest) == 14 and last["id"] not in {r["id"] for r in rest}
    assert client.get("/api/v1/compliance/controls/OC-AC-05/results", params={"start": "2026-04-01"}, headers=admin).json() == []
    assert client.get("/api/v1/compliance/controls/OC-NO-99/results", headers=admin).status_code == 404

    deviations = client.get("/api/v1/compliance/controls/OC-AC-05/deviations", params=PERIOD, headers=admin).json()
    assert [(d["status"], d["hours"], d["cause"]) for d in deviations] == [("FAIL", 2.0, "A rule was changed")]
    assert client.get("/api/v1/compliance/controls/OC-AC-05/deviations", params={"end": "2026-03-09"}, headers=admin).json() == []

    customer = login_headers(client, tenant.slug, _customer_person(db_session, tenant))
    assert client.get("/api/v1/compliance/controls/OC-AC-05/results", headers=customer).status_code == 403


def test_mappings_set_imported_and_audited(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="grc-mappings")
    admin = login_headers(client, tenant.slug, users["admin"].email)
    url = "/api/v1/compliance/cuecs/B-AC-5-01/mappings"
    first = client.put(url, json={"mappings": [{"control_ref": "BANK-SOD-1", "owner": "Risk"}, {"control_ref": "BANK-SOD-2"}]}, headers=admin)
    assert first.status_code == 200 and [m["control_ref"] for m in first.json()] == ["BANK-SOD-1", "BANK-SOD-2"]
    second = client.put(url, json={"mappings": [{"control_ref": "BANK-SOD-1", "owner": "Compliance", "auto_accept": True}]}, headers=admin).json()
    assert [(m["control_ref"], m["owner"], m["auto_accept"]) for m in second] == [("BANK-SOD-1", "Compliance", True)]
    actions = db_session.execute(select(AuditLogEntry.action, AuditLogEntry.summary).where(AuditLogEntry.tenant_id == tenant.id)
                                 .where(AuditLogEntry.action.like("compliance.%"))).all()
    assert ("compliance.mapping.changed", "Mappings of B-AC-5-01: removed BANK-SOD-2; updated BANK-SOD-1") in actions
    assert any(a == "compliance.auto_accept.changed" for a, _s in actions)
    assert db_session.execute(select(SecurityEvent).where(SecurityEvent.tenant_id == tenant.id,
                                                          SecurityEvent.name == "compliance.auto_accept.changed")).first()

    assert client.put(url, json={"mappings": [{"control_ref": "X"}, {"control_ref": "X"}]}, headers=admin).status_code == 422
    assert client.put(url, json={"mappings": [{"control_ref": "X", "link": "ftp://grc"}]}, headers=admin).status_code == 422
    assert client.put("/api/v1/compliance/cuecs/C-AC-2-01/mappings", json={"mappings": []}, headers=admin).status_code == 404  # a customer's

    csv_body = "id,your_control_id,your_control_owner\nB-AC-2-01,BANK-IAM-07,IT\nB-NOPE-1,BANK-X,IT\n"
    imported = client.post("/api/v1/compliance/mappings/import", files={"file": ("cuecs.csv", csv_body, "text/csv")}, headers=admin).json()
    assert imported["added"] == 1 and len(imported["problems"]) == 1
    listed = client.get("/api/v1/compliance/mappings", headers=admin).json()
    assert [m["cuec_id"] for m in listed] == ["B-AC-2-01", "B-AC-5-01"]
    changed = client.get("/api/v1/compliance/mappings", params={"updated_since": "2000-01-01T00:00:00Z"}, headers=admin)
    assert len(changed.json()) == 2

    viewer = login_headers(client, tenant.slug, users["viewer"].email)
    assert client.put(url, json={"mappings": []}, headers=viewer).status_code == 403


def test_a_quarter_mapped_pulled_and_verified_by_a_read_only_client(client, db_session, tenant_factory):
    """The GRC pattern: map, let OpenClear propose and auto-accept, pull by your control id, verify offline."""
    tenant, _account, users = tenant_factory.make(slug="grc-pull")
    admin = login_headers(client, tenant.slug, users["admin"].email)
    client.put("/api/v1/compliance/cuecs/B-AC-5-01/mappings", json={"mappings": [{"control_ref": "BANK-SOD-1", "auto_accept": True}]},
               headers=admin)
    _hourly_results(db_session, tenant, "segregation_of_duties")
    evidence = client.get("/api/v1/compliance/cuecs/B-AC-5-01/evidence", params=PERIOD, headers=admin).json()
    assert evidence["proposed_result"] == "operated" and evidence["evidence"]["scheduled_runs_recorded"] == 24
    attestation_service.prepare_automated(db_session, tenant.id, "monthly", DAY, DAY)
    db_session.commit()

    grc = _admin_client(db_session, tenant, client, read_only=True)
    rows = client.get("/api/v1/compliance/attestations", params={"control": "BANK-SOD-1", "status": "accepted"}, headers=grc).json()
    assert [(r["cuec_id"], r["result"], r["auto_accepted"], r["mappings"], r["verified"]) for r in rows] == \
        [("B-AC-5-01", "operated", True, ["BANK-SOD-1"], True)]
    detail = client.get(f"/api/v1/compliance/attestations/{rows[0]['id']}", headers=grc).json()
    signed = detail["signed"]
    assert signed["attestation"]["result"] == "operated" and signed["signature"]["sha256"] == rows[0]["sha256"]
    # Offline: the digest of the canonical body is what was signed.
    import hashlib

    assert hashlib.sha256(evidence_service.canonical(signed["attestation"])).hexdigest() == signed["signature"]["sha256"]
    prepared = client.get("/api/v1/compliance/attestations", params={"status": "prepared"}, headers=grc).json()
    assert prepared and client.post(f"/api/v1/compliance/attestations/{prepared[0]['id']}/accept", headers=grc).status_code == 403  # reads only

    export = client.get("/api/v1/compliance/attestations/export", params={**PERIOD, "format": "json"}, headers=grc)
    body = export.json()
    assert export.status_code == 200 and body["format"] == "openclear-attestations/1" and "BEGIN PUBLIC KEY" in body["public_key"]
    assert [a["attestation"]["cuec"]["id"] for a in body["attestations"]] == ["B-AC-5-01"]
    table = list(csv.DictReader(io.StringIO(client.get("/api/v1/compliance/attestations/export", params={**PERIOD, "format": "csv"},
                                                       headers=grc).text)))
    assert [(r["cuec_id"], r["mappings"], r["verified"], r["csf"]) for r in table] == [("B-AC-5-01", "BANK-SOD-1", "True", "PR.AA-05")]


def test_manual_attestations_through_the_api(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="grc-manual")
    tenant.attest_second_person = True
    db_session.commit()
    admin = login_headers(client, tenant.slug, users["admin"].email)
    form = {"start": "2026-01-01", "end": "2026-03-31", "result": "operated", "statement": "Our quarterly review found no gaps."}
    made = client.post("/api/v1/compliance/cuecs/B-AT-3-01/attestations", data=form,
                       files={"evidence_file": ("review.pdf", b"%PDF-1.4 review", "application/pdf")}, headers=admin)
    assert made.status_code == 201, made.text
    row = made.json()
    assert row["status"] == "prepared" and row["files"][0]["name"] == "review.pdf" and row["verified"] is None
    assert client.post(f"/api/v1/compliance/attestations/{row['id']}/accept", headers=admin).status_code == 409  # a second person
    assert client.post("/api/v1/compliance/cuecs/B-AT-3-01/attestations", data={**form, "end": "2999-01-01"}, headers=admin).status_code == 422

    second = _admin_client(db_session, tenant, client, read_only=False)
    accepted = client.post(f"/api/v1/compliance/attestations/{row['id']}/accept", headers=second)
    assert accepted.status_code == 200 and accepted.json()["status"] == "accepted" and accepted.json()["verified"] is True
    assert accepted.json()["signed"]["attestation"]["files"][0]["sha256"] == row["files"][0]["sha256"]

    other = client.post("/api/v1/compliance/cuecs/B-AT-3-01/attestations", data={**form, "result": "not_operated"}, headers=admin).json()
    assert client.post(f"/api/v1/compliance/attestations/{other['id']}/reject", json={"reason": ""}, headers=second).status_code == 422
    rejected = client.post(f"/api/v1/compliance/attestations/{other['id']}/reject", json={"reason": "Wrong quarter"}, headers=second).json()
    assert rejected["status"] == "rejected" and rejected["rejected_reason"] == "Wrong quarter"
    assert client.post(f"/api/v1/compliance/attestations/{other['id']}/accept", headers=second).status_code == 409


def test_a_customer_sees_only_its_own(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="grc-customer")
    admin = login_headers(client, tenant.slug, users["admin"].email)
    customer = login_headers(client, tenant.slug, _customer_person(db_session, tenant))
    client.put("/api/v1/compliance/cuecs/B-AC-5-01/mappings", json={"mappings": [{"control_ref": "BANK-SOD-1"}]}, headers=admin)
    form = {"start": "2026-01-01", "end": "2026-03-31", "result": "operated", "statement": "We review our people monthly."}
    mine = client.post("/api/v1/compliance/cuecs/C-AC-2-01/attestations", data=form, headers=customer)
    assert mine.status_code == 201 and mine.json()["customer_id"]
    assert client.post("/api/v1/compliance/cuecs/B-AT-3-01/attestations", data=form, headers=customer).status_code == 404
    assert client.get("/api/v1/compliance/mappings", headers=customer).json() == []
    assert [a["cuec_id"] for a in client.get("/api/v1/compliance/attestations", headers=customer).json()] == ["C-AC-2-01"]
    assert client.get("/api/v1/compliance/attestations", headers=admin).json() == []  # the bank's own
    by_customer = client.get("/api/v1/compliance/attestations", params={"customer_id": mine.json()["customer_id"]}, headers=admin).json()
    assert [a["id"] for a in by_customer] == [mine.json()["id"]]
    assert client.get("/api/v1/compliance/attestations", params={"customer_id": mine.json()["customer_id"]}, headers=customer).status_code == 403
    evidence = client.get("/api/v1/compliance/cuecs/C-AC-2-01/evidence", params={"start": "2026-01-01", "end": "2026-03-31"}, headers=customer)
    assert evidence.status_code == 200 and evidence.json()["proposed_result"] is None
    assert client.get("/api/v1/compliance/oscal/assessment-results", params=PERIOD, headers=customer).status_code == 403


def test_the_evidence_export_carries_the_periods_testing(db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="grc-evidence")
    attestation_service.add_mapping(db_session, tenant.id, None, "B-AC-5-01", control_ref="BANK-SOD-1", auto_accept=True)
    _hourly_results(db_session, tenant, "segregation_of_duties")
    _deviation(db_session, tenant, hours=2)
    attestation_service.prepare_automated(db_session, tenant.id, "monthly", DAY, DAY)
    db_session.commit()
    testing = evidence_service.build(db_session, tenant.id, start=DAY, end=DAY, generated_by_user_id=users["admin"].id)["control_testing"]
    sod = next(c for c in testing["checks"] if c["check"] == "segregation_of_duties")
    assert sod["results"] == 24 and sod["statuses"]["PASS"] == 24 and len(sod["results_sha256"]) == 64
    assert [d["check"] for d in testing["deviations"]] == ["segregation_of_duties"]
    assert [(a["cuec"], a["result"], a["auto_accepted"]) for a in testing["attestations"]] == [("B-AC-5-01", "operated", True)]
    later = evidence_service.build(db_session, tenant.id, start=DAY + timedelta(days=1), end=DAY + timedelta(days=1), generated_by_user_id=None)
    assert later["control_testing"] == {"checks": [], "deviations": [], "attestations": []}


def test_the_guides_offline_check_works(client, db_session, tenant_factory):
    """docs/guides/grc.md's verify() is run as it's printed."""
    import re
    from pathlib import Path

    guide = (Path(__file__).resolve().parents[2] / "docs" / "guides" / "grc.md").read_text()
    code = re.search(r"```python\n(.*?)```", guide, re.S).group(1)
    scope: dict = {}
    exec(code, scope)  # noqa: S102 -- our own documentation
    tenant, _account, users = tenant_factory.make(slug="grc-guide")
    attestation_service.add_mapping(db_session, tenant.id, None, "B-AC-5-01", control_ref="BANK-SOD-1", auto_accept=True)
    _hourly_results(db_session, tenant, "segregation_of_duties")
    attestation_service.prepare_automated(db_session, tenant.id, "monthly", DAY, DAY)
    db_session.commit()
    admin = login_headers(client, tenant.slug, users["admin"].email)
    accepted = client.get("/api/v1/compliance/attestations", params={"status": "accepted"}, headers=admin).json()[0]
    signed = client.get(f"/api/v1/compliance/attestations/{accepted['id']}", headers=admin).json()["signed"]
    assert scope["verify"](signed, signed["signature"]["public_key"]) is True
    signed["attestation"]["result"] = "not_operated"
    assert scope["verify"](signed, signed["signature"]["public_key"]) is False
