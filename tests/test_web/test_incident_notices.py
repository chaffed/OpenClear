# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Incident contacts and notices (REGULATORY_PLAN.md RG3; 12 CFR 304.24, 23 NYCRR 500.11(b)(3)):
a bank names who hears of an incident, the operator sends a notice to the banks affected, it's
emailed to the contacts whatever their settings and shown to the bank's administrators until one
acknowledges it, and both ends are recorded."""

from datetime import date, timedelta

from sqlalchemy import select

from openclear.domain.audit_log_entry import AuditLogEntry
from openclear.domain.notification import Notification, NotificationType
from openclear.domain.security_event import SecurityEvent
from openclear.services import control_checks, evidence_service, incident_service
from tests.test_web.test_staff_and_operator import _login, _platform_login, _staff


def _contacts(db_session, tenant, user_id=None):
    incident_service.set_contacts(db_session, tenant.id, [
        incident_service.ContactInput("contact", "Ira Incident", "ira@bank.example", "Head of IT", "+1 555 0100"),
        incident_service.ContactInput("ceo", "Cara Chief", "ceo@bank.example"),
    ], user_id=user_id)
    db_session.commit()


def test_a_bank_names_its_contacts_and_the_check_watches_them(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="inc-contacts")
    assert control_checks.incident_contacts(db_session, tenant.id).status == "WARN"
    assert incident_service.recipients(db_session, tenant.id) == [users["admin"].email]  # nobody named: the administrators

    csrf = _login(client, tenant.slug, users["admin"].email)
    saved = client.post("/ui/settings/incident-contacts", data={
        "csrf_token": csrf, "contact_0_name": "Ira Incident", "contact_0_email": "ira@bank.example", "contact_0_role": "Head of IT",
        "ceo_name": "Cara Chief", "ceo_email": "ceo@bank.example", "cio_name": "Cy Info", "cio_email": "cio@bank.example"})
    assert saved.status_code in (200, 303)
    db_session.expire_all()
    assert incident_service.recipients(db_session, tenant.id) == ["ira@bank.example"]
    assert control_checks.incident_contacts(db_session, tenant.id).status == "PASS"
    changed = db_session.execute(select(SecurityEvent).where(SecurityEvent.name == "incident_contact.changed",
                                                             SecurityEvent.tenant_id == tenant.id)).scalars().first()
    assert changed is not None and changed.severity == "high"

    # The rule's own fallback is enough on its own (REVIEW_1_33_PLAN.md G3); nobody at all isn't.
    only = client.post("/ui/settings/incident-contacts", data={"csrf_token": csrf, "ceo_name": "Only", "ceo_email": "ceo@bank.example",
                                                                 "cio_name": "Cy", "cio_email": "cio@bank.example"})
    assert only.status_code in (200, 303) and "Name at least one" not in only.text
    db_session.expire_all()
    assert incident_service.recipients(db_session, tenant.id) == ["ceo@bank.example", "cio@bank.example"]
    assert control_checks.incident_contacts(db_session, tenant.id).status == "PASS"
    refused = client.post("/ui/settings/incident-contacts", data={"csrf_token": csrf})
    assert "Name at least one incident contact, or your CEO and CIO" in refused.text
    confirmed = client.post("/ui/settings/incident-contacts", data={"csrf_token": csrf, "confirm_only": "1"})
    assert confirmed.status_code in (200, 303)
    names = set(db_session.execute(select(SecurityEvent.name).where(SecurityEvent.tenant_id == tenant.id)).scalars())
    assert "incident_contact.confirmed" in names  # the yearly attestation reaches the SIEM too (G8)


def test_the_sent_table_says_who_was_told_and_offboarding_isnt_a_kind_to_pick(client, db_session, tenant_factory):
    """REVIEW_1_33_PLAN.md G4, G5."""
    told, _a, _users = tenant_factory.make(slug="inc-told")
    _contacts(db_session, told)
    operator = _staff(db_session, "ops2@incidents.example", "operator")
    _platform_login(client, operator.email)
    page = client.get("/ui/platform/incidents").text
    assert "Offboarding" not in page
    csrf = client.cookies.get("csrf_token")
    sent = client.post("/ui/platform/incidents", data={"csrf_token": csrf, "kind": "maintenance", "title": "Sunday window",
                                                       "message": "Down 02:00–03:00 UTC.", "tenant_ids": str(told.id)}, follow_redirects=False)
    assert sent.status_code == 303
    by_hand = client.post("/ui/platform/incidents", data={"csrf_token": csrf, "kind": "offboarding", "title": "x", "message": "y",
                                                          "tenant_ids": str(told.id)}, follow_redirects=False)
    assert "Choose+the+kind" in by_hand.headers["location"] or "Choose%20the%20kind" in by_hand.headers["location"]
    page = client.get("/ui/platform/incidents").text
    assert "Banks told" in page and f"{told.name}: 1 address" in page


def test_the_operator_notifies_the_banks_affected_and_each_acknowledges(client, db_session, tenant_factory):
    affected, _a, users = tenant_factory.make(slug="inc-affected")
    other, _b, other_users = tenant_factory.make(slug="inc-other")
    _contacts(db_session, affected)
    operator = _staff(db_session, "ops@incidents.example", "operator")

    _platform_login(client, operator.email)
    page = client.get("/ui/platform/incidents")
    assert page.status_code == 200 and affected.name in page.text
    csrf = client.cookies.get("csrf_token")
    sent = client.post("/ui/platform/incidents", data={
        "csrf_token": csrf, "kind": "incident", "title": "Decision files delayed", "message": "The outbox is stalled; decide by hand.",
        "services_affected": "decision files", "tenant_ids": [str(affected.id)]}, follow_redirects=False)
    assert sent.status_code == 303

    emails = db_session.execute(select(Notification).where(Notification.notification_type == NotificationType.INCIDENT_NOTICE)).scalars().all()
    assert [(e.tenant_id, e.destination) for e in emails] == [(affected.id, "ira@bank.example")]
    assert "Decision files delayed" in emails[0].subject and "decide by hand" in emails[0].body
    assert db_session.execute(select(AuditLogEntry).where(AuditLogEntry.tenant_id == affected.id,
                                                          AuditLogEntry.action == "incident.notice.sent")).scalar_one()
    assert db_session.execute(select(SecurityEvent).where(SecurityEvent.name == "incident.notice.sent")).scalars().first().severity == "critical"
    assert control_checks.incident_contacts(db_session, affected.id).status == "WARN"  # not acknowledged yet

    client.cookies.clear()
    _login(client, other.slug, other_users["admin"].email)
    assert "Decision files delayed" not in client.get("/ui/settings").text  # another bank never sees it
    client.cookies.clear()
    csrf = _login(client, affected.slug, users["admin"].email)
    home = client.get("/ui/settings").text
    assert "Decision files delayed" in home and "from your service provider" in home
    [(delivery, _notice)] = incident_service.unacknowledged(db_session, affected.id)
    client.post(f"/ui/incident-notices/{delivery.id}/acknowledge", data={"csrf_token": csrf, "next": "/ui/settings"})
    db_session.expire_all()
    assert incident_service.unacknowledged(db_session, affected.id) == []
    assert "Decision files delayed" not in client.get("/ui/settings").text
    assert control_checks.incident_contacts(db_session, affected.id).status == "PASS"

    today = date.today()
    [evidence] = evidence_service.build(db_session, affected.id, start=today - timedelta(days=1), end=today + timedelta(days=1),
                                        generated_by_user_id=None)["incident_notices"]
    assert evidence["emailed_to"] == ["ira@bank.example"] and evidence["acknowledged_at"]


def test_only_operators_issue_notices_and_only_a_banks_admins_acknowledge(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="inc-who")
    notice = incident_service.issue(db_session, kind="maintenance", title="Upgrade Saturday", message="Down 02:00-03:00 UTC.",
                                    tenant_ids=[tenant.id], user_id=None)
    db_session.commit()
    [(delivery, _n)] = incident_service.unacknowledged(db_session, tenant.id)
    assert notice.kind == "maintenance"

    csrf = _login(client, tenant.slug, users["viewer"].email)
    assert "Upgrade Saturday" not in client.get("/ui/").text  # the banner is for administrators
    assert client.post(f"/ui/incident-notices/{delivery.id}/acknowledge", data={"csrf_token": csrf},
                       follow_redirects=False).status_code == 403
    client.cookies.clear()
    assert client.get("/ui/platform/incidents", follow_redirects=False).status_code in (302, 303, 401, 403)
