# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Customers' own CUECs (CONTROLS_PLAN.md CT-D13): the bank's roll-up, and asking customers to
attest each year."""

from datetime import date

from sqlalchemy import select

from openclear.domain.audit_log_entry import AuditLogEntry
from openclear.domain.notification import Notification, NotificationType
from openclear.services import attestation_service, security_group_service, user_service
from tests.conftest import TenantFactory, login_headers
from tests.test_ml.test_per_customer_ml import _make_customer_with_account
from tests.test_web.test_web_bulk_ach import _login

TODAY = date(2027, 1, 2)


def _customer_with_people(db_session, tenant, number: str):
    customer, _account = _make_customer_with_account(db_session, tenant, number)
    admin = next(g for g in security_group_service.list_security_groups(db_session, tenant.id) if g.name == "Admin")
    viewer = next(g for g in security_group_service.list_security_groups(db_session, tenant.id) if g.name == "Viewer")
    attester = user_service.create_user_with_membership(db_session, tenant.id, email=f"cfo-{number}@customer.example.com",
                                                        password=TenantFactory.PASSWORD, security_group_id=admin.id, customer_id=customer.id)
    user_service.create_user_with_membership(db_session, tenant.id, email=f"clerk-{number}@customer.example.com",
                                             password=TenantFactory.PASSWORD, security_group_id=viewer.id, customer_id=customer.id)
    db_session.commit()
    return customer, attester


def test_the_yearly_request_goes_once_to_who_can_attest(db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="ct-d13-ask")
    customer, attester = _customer_with_people(db_session, tenant, "D13-1")
    assert attestation_service.ask_customers_due(db_session, TODAY) == 0  # off by default
    tenant.ask_customers_to_attest = True
    db_session.commit()

    assert attestation_service.ask_customers_due(db_session, TODAY) == 1
    notes = db_session.execute(select(Notification).where(Notification.notification_type == NotificationType.ATTESTATION_REQUESTED)).scalars().all()
    assert [n.recipient_user_id for n in notes] == [attester.id]  # not the clerk, and not the bank's own staff
    assert "for 2026-01-01 to 2026-12-31" in notes[0].body and customer.name in notes[0].body
    assert db_session.get(type(tenant), tenant.id).customers_asked_for_year == 2026
    logged = db_session.execute(select(AuditLogEntry.summary).where(AuditLogEntry.tenant_id == tenant.id,
                                                                    AuditLogEntry.action == "compliance.customers.asked")).scalars().all()
    assert logged == ["Asked 1 people at 1 customers to attest their responsibilities for 2026"]
    assert attestation_service.ask_customers_due(db_session, date(2027, 6, 1)) == 0  # once a year
    assert attestation_service.ask_customers_due(db_session, date(2028, 1, 1)) == 1


def test_the_banks_roll_up(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="ct-d13-rollup")
    customer, attester = _customer_with_people(db_session, tenant, "D13-2")
    _quiet, _nobody = _make_customer_with_account(db_session, tenant, "D13-3")
    db_session.commit()
    made = attestation_service.record_manual(db_session, tenant.id, customer.id, "C-AC-2-01", start=date(2025, 1, 1), end=date(2025, 12, 31),
                                             result="operated", statement="Reviewed monthly.", user_id=attester.id)
    attestation_service.accept(db_session, made, user_id=attester.id)
    attestation_service.record_manual(db_session, tenant.id, customer.id, "C-SI-4-01", start=date(2025, 1, 1), end=date(2025, 12, 31),
                                      result="operated", statement="Decided daily.", user_id=attester.id)
    db_session.commit()

    rows = {r["name"]: r for r in attestation_service.customer_rollup(db_session, tenant.id, date(2025, 1, 1), date(2025, 12, 31))}
    mine = rows[customer.name]
    assert (mine["attested"], mine["waiting"], mine["can_attest"], mine["cuecs"]) == (1, 1, 1, 15) and mine["last_accepted_at"]
    assert rows[_quiet.name]["can_attest"] == 0 and rows[_quiet.name]["attested"] == 0

    admin = login_headers(client, tenant.slug, users["admin"].email)
    api = client.get("/api/v1/compliance/customers", params={"start": "2025-01-01", "end": "2025-12-31"}, headers=admin).json()
    assert {r["name"]: r["attested"] for r in api} == {customer.name: 1, _quiet.name: 0}
    their = login_headers(client, tenant.slug, attester.email)
    assert client.get("/api/v1/compliance/customers", headers=their).status_code == 403


def test_the_page_setting_and_asking_now(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="ct-d13-page")
    customer, _attester = _customer_with_people(db_session, tenant, "D13-4")
    csrf = _login(client, tenant.slug, users["admin"].email)
    page = client.get("/ui/compliance/cuecs")
    assert "Your customers' responsibilities in" in page.text and customer.name in page.text
    saved = client.post("/ui/compliance/settings", data={"csrf_token": csrf, "attestation_period": "quarterly",
                                                         "ask_customers_to_attest": "true"}, follow_redirects=False)
    assert saved.status_code == 303
    db_session.expire_all()
    assert db_session.get(type(tenant), tenant.id).ask_customers_to_attest is True
    asked = client.post("/ui/compliance/customers/ask", data={"csrf_token": csrf}, follow_redirects=False)
    assert asked.status_code == 303 and "Asked%201%20people" in asked.headers["location"]
    assert db_session.execute(select(Notification).where(Notification.notification_type == NotificationType.ATTESTATION_REQUESTED)).first()
