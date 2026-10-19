# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""CUEC mappings and attestations (CONTROLS_PLAN.md CT4; services/attestation_service.py)."""

import copy
import json
from datetime import date, datetime, time, timedelta, timezone

import pytest
from sqlalchemy import select

from openclear.compliance import cuecs
from openclear.config import get_settings
from openclear.domain.control_result import ControlDeviation, ControlTestResult
from openclear.domain.cuec_attestation import CuecAttestation
from openclear.domain.security_event import SecurityEvent
from openclear.services import attestation_service, security_group_service, user_service
from openclear.services.attestation_service import AttestationError
from tests.conftest import TenantFactory
from tests.test_web.test_web_bulk_ach import _login

DAY = date(2026, 3, 10)


def _hourly_results(db_session, tenant, key, *, hours=24, status="PASS", day=DAY):
    start = datetime.combine(day, time.min, tzinfo=timezone.utc)
    for h in range(hours):
        db_session.add(ControlTestResult(tenant_id=tenant.id, check_key=key, status=status, summary="x", evidence={}, evidence_sha256=f"{h:064d}",
                                         started_by="schedule", duration_ms=1, recorded_at=start + timedelta(hours=h, minutes=5)))
    db_session.commit()


def test_periods():
    assert attestation_service.period_containing("quarterly", date(2026, 5, 17)) == (date(2026, 4, 1), date(2026, 6, 30))
    assert attestation_service.period_containing("monthly", date(2026, 2, 3)) == (date(2026, 2, 1), date(2026, 2, 28))
    assert attestation_service.previous_period("yearly", date(2026, 1, 1)) == (date(2025, 1, 1), date(2025, 12, 31))
    assert attestation_service.previous_period("quarterly", date(2026, 4, 1)) == (date(2026, 1, 1), date(2026, 3, 31))


def test_the_automated_rule(db_session, tenant_factory):
    tenant, _account, _users = tenant_factory.make(slug="att-rule")
    sod = cuecs.by_id()["B-AC-5-01"]  # evidenced by the hourly segregation_of_duties check
    _hourly_results(db_session, tenant, "segregation_of_duties", hours=22)  # 22 of 24: under 95%
    result, evidence = attestation_service.evaluate(db_session, tenant.id, sod, DAY, DAY)
    assert result == "not_tested" and evidence["scheduled_runs_recorded"] == 22 and evidence["scheduled_runs_expected"] == 24

    _hourly_results(db_session, tenant, "segregation_of_duties", hours=2, day=DAY)
    assert attestation_service.evaluate(db_session, tenant.id, sod, DAY, DAY)[0] == "operated"
    opened = datetime.combine(DAY, time(1), tzinfo=timezone.utc)
    some_result = db_session.execute(select(ControlTestResult).where(ControlTestResult.tenant_id == tenant.id)).scalars().first()
    db_session.add(ControlDeviation(tenant_id=tenant.id, check_key="segregation_of_duties", status="FAIL", opened_at=opened,
                                    opened_result_id=some_result.id, closed_at=opened + timedelta(hours=30)))
    db_session.commit()
    result, evidence = attestation_service.evaluate(db_session, tenant.id, sod, DAY, DAY)
    assert result == "operated_with_exceptions" and evidence["deviations"][0]["beyond_grace"] is True


def test_proposals_auto_accept_and_signatures(db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="att-auto")
    attestation_service.add_mapping(db_session, tenant.id, None, "B-AC-5-01", control_ref="BANK-SOD-1", auto_accept=True)
    _hourly_results(db_session, tenant, "segregation_of_duties")
    made = attestation_service.prepare_automated(db_session, tenant.id, "monthly", DAY, DAY)
    db_session.commit()
    automated = {c.id for c in cuecs.CUECS if c.evidence == "automated" and c.party in ("bank", "host") and not c.withdrawn}
    assert {a.cuec_id for a in made} == automated
    assert attestation_service.prepare_automated(db_session, tenant.id, "monthly", DAY, DAY) == []  # not twice
    sod = next(a for a in made if a.cuec_id == "B-AC-5-01")
    assert sod.status == "accepted" and sod.auto_accepted and sod.result == "operated"
    assert attestation_service.verify(sod)
    assert sod.signed_body["mappings"] == [{"control": "BANK-SOD-1", "name": None, "owner": None}]
    other = next(a for a in made if a.cuec_id != "B-AC-5-01")
    assert other.status == "prepared" and other.signature is None

    tampered = copy.deepcopy(sod)
    tampered.signed_body = {**sod.signed_body, "result": "operated_with_exceptions"}
    assert not attestation_service.verify(tampered)


def test_manual_attestations_a_second_person_and_corrections(db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="att-manual")
    tenant.attest_second_person = True
    db_session.commit()
    first = attestation_service.record_manual(db_session, tenant.id, None, "B-RA-3-01", start=date(2026, 1, 1), end=date(2026, 3, 31),
                                              result="operated", statement="Risk assessment approved by the board in March.",
                                              user_id=users["admin"].id)
    with pytest.raises(AttestationError, match="second person"):
        attestation_service.accept(db_session, first, user_id=users["admin"].id)
    attestation_service.accept(db_session, first, user_id=users["approver"].id)
    correction = attestation_service.record_manual(db_session, tenant.id, None, "B-RA-3-01", start=date(2026, 1, 1), end=date(2026, 3, 31),
                                                   result="operated_with_exceptions", statement="Approved late, in April.",
                                                   user_id=users["approver"].id)
    attestation_service.accept(db_session, correction, user_id=users["admin"].id)
    db_session.commit()
    assert first.status == "superseded" and correction.supersedes_id == first.id and attestation_service.verify(correction)
    with pytest.raises(AttestationError):
        attestation_service.record_manual(db_session, tenant.id, None, "B-RA-3-01", start=date(2026, 1, 1),
                                          end=date.today() + timedelta(days=5), result="operated", statement="x", user_id=users["admin"].id)


def test_mappings_from_the_csv(db_session, tenant_factory):
    tenant, _account, _users = tenant_factory.make(slug="att-csv")
    content = ("id,party,title,your_control_id,your_control_owner\n"
               "B-AC-5-01,bank,x,BANK-SOD-1,Risk team\nB-AC-2-01,bank,x,,\nC-IA-2-01,customer,x,CUST-1,\nNOPE-1,bank,x,X-1,\n").encode()
    added, problems = attestation_service.import_csv(db_session, tenant.id, None, content, user_id=None)
    assert added == 1 and len(problems) == 2  # a customer's CUEC isn't the bank's to map; an unknown id
    assert attestation_service.import_csv(db_session, tenant.id, None, content, user_id=None)[0] == 0  # not twice
    [mapping] = attestation_service.mappings(db_session, tenant.id, None)
    assert (mapping.cuec_id, mapping.control_ref, mapping.owner) == ("B-AC-5-01", "BANK-SOD-1", "Risk team")


def test_the_pages(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="att-web")
    csrf = _login(client, tenant.slug, users["admin"].email)
    page = client.get("/ui/compliance/cuecs").text
    assert "B-AC-5-01" in page and "H-SC-28(1)-01" in page and "C-IA-2-01" not in page
    client.post("/ui/compliance/cuecs/B-AC-5-01/mappings", data={"csrf_token": csrf, "control_ref": "BANK-SOD-1", "owner": "Risk"})
    [mapping] = attestation_service.mappings(db_session, tenant.id, None)
    client.post(f"/ui/compliance/mappings/{mapping.id}/auto-accept", data={"csrf_token": csrf, "on": "true"})
    db_session.expire_all()
    assert db_session.execute(select(SecurityEvent).where(SecurityEvent.tenant_id == tenant.id,
                                                          SecurityEvent.name == "compliance.auto_accept.changed")).scalar_one()
    client.post("/ui/compliance/cuecs/B-RA-3-01/attestations",
                data={"csrf_token": csrf, "start": "2026-01-01", "end": "2026-03-31", "result": "operated", "statement": "Board approved."},
                files={"evidence_file": ("minutes.pdf", b"%PDF-1.4 minutes", "application/pdf")})
    db_session.expire_all()
    manual = db_session.execute(select(CuecAttestation).where(CuecAttestation.cuec_id == "B-RA-3-01")).scalar_one()
    assert manual.files[0]["name"] == "minutes.pdf" and manual.status == "prepared"
    assert "Say+why" in client.post(f"/ui/compliance/attestations/{manual.id}/reject", data={"csrf_token": csrf, "reason": ""},
                                      follow_redirects=False).headers["location"]
    client.post(f"/ui/compliance/attestations/{manual.id}/accept", data={"csrf_token": csrf})
    signed = client.get(f"/ui/compliance/attestations/{manual.id}.json")
    assert signed.status_code == 200
    body = signed.json()
    assert body["attestation"]["cuec"]["id"] == "B-RA-3-01" and body["signature"]["sha256"] and "BEGIN PUBLIC KEY" in body["signature"]["public_key"]
    assert "signature verifies" in client.get("/ui/compliance/cuecs/B-RA-3-01").text

    _login(client, tenant.slug, users["viewer"].email)
    assert client.get("/ui/compliance/cuecs").status_code == 403  # compliance:read is the admin's by default


def test_a_customer_sees_its_own_responsibilities(client, db_session, tenant_factory):
    from tests.test_ml.test_per_customer_ml import _make_customer_with_account

    tenant, _account, _users = tenant_factory.make(slug="att-cust")
    customer, _acct = _make_customer_with_account(db_session, tenant, "AT-1")
    group = security_group_service.create_security_group(db_session, tenant.id, security_group_service.SecurityGroupInput(
        name="Customer compliance", permissions=["compliance:read", "compliance:attest", "compliance:manage"], offered_to_customers=True))
    user_service.create_user_with_membership(db_session, tenant.id, email="cco@cust.example.com", password=TenantFactory.PASSWORD,
                                             security_group_id=group.id, customer_id=customer.id)
    db_session.commit()
    csrf = _login(client, tenant.slug, "cco@cust.example.com")
    page = client.get("/ui/compliance/cuecs").text
    assert "Your responsibilities" in page and "C-IA-2-01" in page and "B-AC-5-01" not in page
    assert client.get("/ui/compliance/cuecs/B-AC-5-01").status_code == 404
    client.post("/ui/compliance/cuecs/C-IA-2-01/attestations",
                data={"csrf_token": csrf, "start": "2026-01-01", "end": "2026-03-31", "result": "operated", "statement": "All our people use keys."})
    db_session.expire_all()
    mine = db_session.execute(select(CuecAttestation).where(CuecAttestation.cuec_id == "C-IA-2-01")).scalar_one()
    assert mine.customer_id == customer.id and mine.tenant_id == tenant.id
    assert attestation_service.attestations(db_session, tenant.id, None, cuec_id="C-IA-2-01") == []  # the bank's scope is apart


def test_the_hosts_cuecs_attested_by_the_platform(db_session, tenant_factory, monkeypatch):
    tenant, _account, users = tenant_factory.make(slug="att-host")
    monkeypatch.setattr(get_settings(), "host_cuecs_attested_by", "platform")
    assert "host" not in attestation_service.parties_for(None)
    with pytest.raises(AttestationError, match="platform staff"):
        attestation_service.record_manual(db_session, tenant.id, None, "H-CP-9-01", start=date(2026, 1, 1), end=date(2026, 3, 31),
                                          result="operated", statement="Backups tested.", user_id=users["admin"].id)
    made = attestation_service.record_installation(db_session, "H-CP-9-01", start=date(2026, 1, 1), end=date(2026, 3, 31), result="operated",
                                                   statement="Restore tested 2026-03-02.", user_id=users["admin"].id)
    assert made.status == "prepared"  # another of the platform's staff accepts it (DUAL_CONTROL_PLAN.md DC4)
    with pytest.raises(AttestationError, match="Someone else"):
        attestation_service.accept_installation(db_session, made, user_id=users["admin"].id)
    attestation_service.accept_installation(db_session, made, user_id=users["approver"].id)
    db_session.commit()
    assert made.tenant_id is None and made.status == "accepted" and attestation_service.verify(made)
    assert attestation_service.get_attestation(db_session, tenant.id, None, made.id) == made  # every bank reads it
    proposed = attestation_service.prepare_automated(db_session, tenant.id, "quarterly", date(2026, 1, 1), date(2026, 3, 31))
    assert not [a for a in proposed if a.cuec_id.startswith("H-")]


def test_the_period_job(db_session, tenant_factory, monkeypatch):
    from openclear.services import webhook_service

    tenant, _account, _users = tenant_factory.make(slug="att-job")
    sent = []
    monkeypatch.setattr(webhook_service, "emit", lambda session, tenant_id, event, data: sent.append(event))
    assert attestation_service.prepare_due(db_session, today=date(2026, 5, 2)) == 0  # mid-quarter
    made = attestation_service.prepare_due(db_session, today=date(2026, 4, 1))  # the first quarter ended yesterday
    assert made and "compliance.attestation.prepared" in sent
    rows = attestation_service.attestations(db_session, tenant.id, None)
    assert {(a.period_start, a.period_end) for a in rows} == {(date(2026, 1, 1), date(2026, 3, 31))}
    assert json.loads(json.dumps(rows[0].evidence))  # plain JSON


def test_sandbox_banks_are_left_out_unless_the_installation_includes_them(db_session, tenant_factory, monkeypatch):
    tenant, _account, _users = tenant_factory.make(slug="att-sandbox")
    tenant.is_sandbox, tenant.attestation_period = True, "monthly"
    db_session.commit()
    first = date(2026, 4, 1)  # the night after March ended
    assert attestation_service.prepare_due(db_session, first) == 0
    monkeypatch.setattr(get_settings(), "control_tests_include_sandboxes", True)
    assert attestation_service.prepare_due(db_session, first) > 0
