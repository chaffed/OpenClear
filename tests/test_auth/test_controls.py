# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Continuous control monitoring (COMPLIANCE_PLAN.md C4; services/controls_service.py)."""

from datetime import datetime, timedelta, timezone

from openclear.auth import sod
from openclear.config import get_settings
from openclear.domain.audit_log_entry import AuditLogEntry
from openclear.services import controls_service, mrm_service
from tests.conftest import TenantFactory, login_headers
from tests.test_auth.test_sod import _separate_duties


def _by_key(db_session, tenant):
    return {c.key: c for c in controls_service.status(db_session, tenant.id)}


def test_a_new_bank_s_controls(db_session, tenant_factory):
    tenant, _account, _users = tenant_factory.make(slug="controls-new")
    controls = _by_key(db_session, tenant)
    assert controls["audit_chain"].status == "PASS"
    assert controls["parser_hardening"].status == "PASS" and controls["parser_hardening"].evidence["files"] >= 5
    assert controls["field_encryption"].status == "PASS"
    assert controls["sla"].status == "PASS"
    # Not yet done: warn mode with conflicting groups, no anomaly run, admins without keys, no attestation.
    assert controls["segregation_of_duties"].status == "WARN" and "Admin" in controls["segregation_of_duties"].detail
    assert controls["anomaly_capture"].status == "WARN"
    assert controls["admin_mfa"].status == "WARN"
    assert controls["storage_encryption"].status == "WARN"
    assert controls["production_safety"].status == "WARN"  # the test suite runs as development
    assert controls_service.summary(list(controls.values()))["overall"] == "WARN"


def test_controls_move_to_pass_and_fail(db_session, tenant_factory, monkeypatch):
    tenant, _account, _users = tenant_factory.make(slug="controls-move")
    _separate_duties(db_session, tenant)
    sod.set_mode(db_session, tenant.id, "enforce")
    mrm_service.anomaly_capture(db_session, tenant.id, user_id=None)
    monkeypatch.setattr(get_settings(), "storage_encryption_attestation", "RDS storage encryption, KMS alias/openclear")
    db_session.commit()
    controls = _by_key(db_session, tenant)
    assert controls["segregation_of_duties"].status == "PASS"
    assert controls["anomaly_capture"].status == "PASS"
    assert controls["storage_encryption"].status == "PASS" and "KMS" in controls["storage_encryption"].detail

    # A stale anomaly run warns; a tampered action log fails.
    run = mrm_service.latest(db_session, tenant.id, mrm_service.ANOMALY_CAPTURE)
    run.started_at = datetime.now(timezone.utc) - timedelta(days=2)
    entry = db_session.query(AuditLogEntry).filter(AuditLogEntry.tenant_id == tenant.id).first()
    entry.summary = entry.summary + " (edited)"
    db_session.commit()
    # The control shows the latest verification (services/long_runs.py); the next one finds the edit.
    from openclear.services import long_runs

    long_runs.verify_now(db_session, tenant.id)
    db_session.commit()
    controls = _by_key(db_session, tenant)
    assert controls["anomaly_capture"].status == "WARN"
    assert controls["audit_chain"].status == "FAIL"
    assert controls_service.summary(list(controls.values()))["overall"] == "FAIL"


def test_overdue_exceptions_fail_the_sla_control(db_session, tenant_factory):
    from datetime import date
    from decimal import Decimal

    from openclear.networks.check.ingestion import PaidItemSubmission, ingest_paid_item
    from openclear.repositories.exception_repo import ExceptionRepository

    tenant, account, _users = tenant_factory.make(slug="controls-sla")
    paid = ingest_paid_item(db_session, tenant.id, PaidItemSubmission(account_id=account.id, check_number="1", presented_amount=Decimal("5"),
                                                                    presented_date=date(2026, 1, 2)))
    exception = ExceptionRepository(db_session, tenant.id).list(source_item_id=paid.id)[0]
    exception.decision_deadline = datetime.now(timezone.utc) - timedelta(minutes=5)
    db_session.commit()
    sla = _by_key(db_session, tenant)["sla"]
    assert sla.status == "FAIL" and sla.evidence["overdue_exceptions"] == 1


def test_the_api_and_the_page(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="controls-api")
    response = client.get("/api/v1/controls/status", headers=login_headers(client, tenant.slug, users["admin"].email))
    assert response.status_code == 200
    body = response.json()
    assert body["overall"] in ("PASS", "WARN", "FAIL") and len(body["controls"]) == 12
    assert {"key", "title", "status", "detail", "evidence"} <= set(body["controls"][0])
    client.get("/ui/login")
    csrf = client.cookies.get("csrf_token")
    client.post("/ui/login", data={"tenant_slug": tenant.slug, "email": users["admin"].email, "password": TenantFactory.PASSWORD,
                                   "csrf_token": csrf, "next": "/ui/"})
    page = client.get("/ui/admin/controls")
    assert page.status_code == 200 and "Action log intact" in page.text and "badge-warn" in page.text
