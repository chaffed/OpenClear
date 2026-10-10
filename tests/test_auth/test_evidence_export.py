# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""The evidence export (COMPLIANCE_PLAN.md C3; services/evidence_service.py, GET /api/v1/audit/export)."""

import hashlib
import json
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from openclear.bulk_import.signing import verify_signature
from openclear.domain.audit_log_entry import AuditLogEntry
from openclear.services import evidence_service, mrm_service
from tests.conftest import TenantFactory, login_headers
from tests.test_auth.test_sod import _present_and_regroup

TODAY = datetime.now(timezone.utc).date()  # periods are UTC days


def test_the_json_export_holds_the_evidence_and_verifies(client, db_session, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="evidence-json")
    exception = _present_and_regroup(client, db_session, tenant, account, users)
    approver = login_headers(client, tenant.slug, users["preparer"].email)  # now an Approver, who presented it: a warn-mode breach
    assert client.post(f"/api/v1/exceptions/{exception.id}/decide", headers=approver,
                       json={"outcome": "pay", "reason_code": "ok"}).status_code in (200, 201)
    mrm_service.anomaly_capture(db_session, tenant.id, user_id=users["admin"].id)
    db_session.commit()

    admin = login_headers(client, tenant.slug, users["admin"].email)
    response = client.get(f"/api/v1/audit/export?start={TODAY.replace(day=1)}&end={TODAY}", headers=admin)
    assert response.status_code == 200, response.text
    signed = response.json()
    bundle = signed["bundle"]
    assert bundle["bank"]["slug"] == tenant.slug and bundle["format"] == "openclear-evidence/1"
    assert bundle["system"]["openclear_version"]
    assert {r["module"] for r in bundle["rules"]["built_in"]} >= {"openclear.networks.check.rules", "openclear.networks.ach.rules"}
    assert all(len(r["sha256"]) == 64 for r in bundle["rules"]["built_in"])
    assert bundle["audit"]["chain_verifies"] is True
    assert len(bundle["audit"]["sod_violations"]) == 1
    [decision] = bundle["decisions"]["items"]
    assert decision["outcome"] == "pay" and decision["decided_by"] == users["preparer"].email
    assert bundle["control_runs"][0]["kind"] == "anomaly_capture"
    assert {c["key"] for c in bundle["controls"]["controls"]} >= {"audit_chain", "segregation_of_duties"}
    assert {g["name"]: g["conflicts"] for g in bundle["settings"]["security_groups"]}["Admin"]

    # It verifies, and any change breaks it.
    assert evidence_service.verify(signed)
    tampered = json.loads(json.dumps(signed))
    tampered["bundle"]["decisions"]["items"][0]["decided_by"] = "someone.else@example.com"
    assert not evidence_service.verify(tampered)
    # The file as sent is signed too.
    assert response.headers["x-openclear-file-sha256"] == hashlib.sha256(response.content).hexdigest()
    assert verify_signature(response.content, response.headers["x-openclear-file-signature"])
    # And the export itself is in the action log.
    exported = db_session.execute(select(AuditLogEntry).where(AuditLogEntry.tenant_id == tenant.id, AuditLogEntry.action == "audit.export")
                                  ).scalars().all()
    assert exported and exported[-1].metadata_json["sha256"] == signed["signature"]["sha256"]


def test_who_may_export_and_bad_periods(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="evidence-perm")
    admin = login_headers(client, tenant.slug, users["admin"].email)
    assert client.get("/api/v1/audit/export?start=2026-02-01&end=2026-01-01", headers=admin).status_code == 422
    assert client.get("/api/v1/audit/export?start=2024-01-01&end=2026-01-01", headers=admin).status_code == 422
    preparer = login_headers(client, tenant.slug, users["preparer"].email)
    allowed = client.get(f"/api/v1/audit/export?start={TODAY}&end={TODAY}", headers=preparer).status_code
    from openclear.services import security_group_service

    perms = security_group_service.get_security_group_by_name(db_session, tenant.id, "Preparer").permissions
    assert allowed == (200 if "audit_log:read" in perms else 403)


def test_the_pdf_export(client, db_session, tenant_factory):
    from openclear.services.doc_pdf_service import weasyprint_usable

    if not weasyprint_usable():
        pytest.skip("WeasyPrint isn't usable here")
    tenant, _account, users = tenant_factory.make(slug="evidence-pdf")
    client.get("/ui/login")
    csrf = client.cookies.get("csrf_token")
    client.post("/ui/login", data={"tenant_slug": tenant.slug, "email": users["admin"].email, "password": TenantFactory.PASSWORD,
                                   "csrf_token": csrf, "next": "/ui/"})
    assert "Export evidence" in client.get("/ui/admin/controls").text
    response = client.post("/ui/admin/controls/evidence", data={"csrf_token": csrf, "start": str(TODAY), "end": str(TODAY), "format": "pdf"})
    assert response.status_code == 200 and response.headers["content-type"] == "application/pdf"
    assert response.content.startswith(b"%PDF") and verify_signature(response.content, response.headers["x-openclear-file-signature"])
