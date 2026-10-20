# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""The bank approves new customer users (MOBILE_PLAN.md R6; services/customer_approval_service.py):
off by default; when on, people a customer's side adds wait, and only the bank lets them in."""

from datetime import datetime, timezone

from sqlalchemy import select

from openclear.domain.audit_log_entry import AuditLogEntry
from openclear.domain.customer_admin import CustomerEmailDomain
from openclear.domain.notification import Notification, NotificationType
from openclear.domain.security_event import SecurityEvent
from openclear.domain.tenant_membership import TenantMembership
from openclear.domain.user import User
from tests.conftest import TenantFactory
from tests.test_web.test_customer_people import _invite, _link, _login, _setup
from tests.test_web.test_customer_sign_on import _connection, _sign_in


def _accept(client, link, password="a-long-new-password-1"):
    client.cookies.clear()
    client.get(link)
    return client.post(link, data={"csrf_token": client.cookies.get("csrf_token"), "password": password, "password_confirm": password},
                       follow_redirects=False)


def _password_login(client, tenant, email, password="a-long-new-password-1"):
    client.cookies.clear()
    client.get("/ui/login")
    return client.post("/ui/login", data={"tenant_slug": tenant.slug, "email": email, "password": password,
                                          "csrf_token": client.cookies.get("csrf_token"), "next": "/ui/"}, follow_redirects=False)


def _membership(db_session, email):
    db_session.expire_all()
    return db_session.execute(select(TenantMembership).join(User, User.id == TenantMembership.user_id)
                              .where(User.email == email)).scalar_one_or_none()


def _events(db_session, tenant, name):
    return db_session.execute(select(SecurityEvent).where(SecurityEvent.tenant_id == tenant.id, SecurityEvent.name == name)).scalars().all()


def test_off_by_default(client, db_session, tenant_factory):
    s = _setup(db_session, tenant_factory, slug="ap-off")
    csrf = _login(client, s["tenant"], s["admin"].email)
    _invite(client, csrf, s, "pat@acme.example.com")
    assert _accept(client, _link(db_session, "pat@acme.example.com")).status_code == 303
    membership = _membership(db_session, "pat@acme.example.com")
    assert membership.is_active and membership.pending_approval_at is None


def test_an_invited_person_waits_until_the_bank_approves(client, db_session, tenant_factory):
    s = _setup(db_session, tenant_factory, slug="ap-yes")
    s["tenant"].approve_customer_users = True
    db_session.commit()
    csrf = _login(client, s["tenant"], s["admin"].email)
    _invite(client, csrf, s, "pat@acme.example.com")
    accepted = _accept(client, _link(db_session, "pat@acme.example.com"))
    assert "approves+new+people+first" in accepted.headers["location"]
    membership = _membership(db_session, "pat@acme.example.com")
    assert not membership.is_active and membership.pending_approval_at is not None

    refused = _password_login(client, s["tenant"], "pat@acme.example.com")
    assert refused.status_code == 401 and "Your bank approves new people" in refused.text
    api = client.post("/api/v1/auth/login", json={"tenant_slug": s["tenant"].slug, "email": "pat@acme.example.com",
                                                   "password": "a-long-new-password-1"})
    assert api.status_code == 403

    # The customer's administrator sees them waiting, and can't let them in.
    csrf = _login(client, s["tenant"], s["admin"].email)
    assert "waiting for your bank" in client.get("/ui/people").text
    reactivated = client.post(f"/ui/people/{membership.id}/reactivate", data={"csrf_token": csrf}, follow_redirects=False)
    assert "only+your+bank" in reactivated.headers.get("location", "") or "only your bank" in reactivated.text
    assert not _membership(db_session, "pat@acme.example.com").is_active

    # The bank: the menu says one is waiting; approving lets them in, and the administrator hears.
    csrf = _login(client, s["tenant"], s["users"]["admin"].email)
    assert 'href="/ui/approvals/customer-users"' in client.get("/ui/").text
    page = client.get("/ui/approvals/customer-users").text
    assert "pat@acme.example.com" in page and "Acme" in page and "Clerk" in page
    assert client.post(f"/ui/approvals/customer-users/{membership.id}/approve", data={"csrf_token": csrf},
                       follow_redirects=False).status_code == 303
    after = _membership(db_session, "pat@acme.example.com")
    assert after.is_active and after.pending_approval_at is None
    assert _password_login(client, s["tenant"], "pat@acme.example.com").status_code == 303
    note = db_session.execute(select(Notification).where(Notification.notification_type == NotificationType.CUSTOMER_USER_DECIDED)).scalars().all()
    assert [n.destination for n in note if n.channel.value == "email"] == [s["admin"].email] and "approved pat@acme.example.com" in note[0].body
    assert _events(db_session, s["tenant"], "user.approved")
    assert db_session.execute(select(AuditLogEntry).where(AuditLogEntry.action == "customer_user.approve",
                                                          AuditLogEntry.resource_id == s["customer"].id)).scalar_one()


def test_rejecting_needs_a_reason_and_removes_their_access(client, db_session, tenant_factory):
    s = _setup(db_session, tenant_factory, slug="ap-no")
    s["tenant"].approve_customer_users = True
    db_session.commit()
    csrf = _login(client, s["tenant"], s["admin"].email)
    _invite(client, csrf, s, "pat@acme.example.com", accounts=[s["payroll"]])
    _accept(client, _link(db_session, "pat@acme.example.com"))
    membership = _membership(db_session, "pat@acme.example.com")

    csrf = _login(client, s["tenant"], s["users"]["admin"].email)
    no_reason = client.post(f"/ui/approvals/customer-users/{membership.id}/reject", data={"csrf_token": csrf, "reason": " "},
                            follow_redirects=False)
    assert "Say+why" in no_reason.headers["location"] and _membership(db_session, "pat@acme.example.com") is not None
    client.post(f"/ui/approvals/customer-users/{membership.id}/reject", data={"csrf_token": csrf, "reason": "Not on our signer list"})
    assert _membership(db_session, "pat@acme.example.com") is None
    assert _password_login(client, s["tenant"], "pat@acme.example.com").status_code == 401
    note = db_session.execute(select(Notification).where(Notification.notification_type == NotificationType.CUSTOMER_USER_DECIDED)).scalars().first()
    assert "Not on our signer list" in note.body
    assert _events(db_session, s["tenant"], "user.rejected")


def test_a_customers_own_setting_overrides_the_banks(client, db_session, tenant_factory):
    s = _setup(db_session, tenant_factory, slug="ap-own")
    csrf = _login(client, s["tenant"], s["users"]["admin"].email)
    client.post(f"/ui/customers/{s['customer'].id}/approval", data={"csrf_token": csrf, "approve_users": "true"})
    db_session.expire_all()
    assert s["customer"].approve_users is True
    csrf = _login(client, s["tenant"], s["admin"].email)
    _invite(client, csrf, s, "pat@acme.example.com")
    _accept(client, _link(db_session, "pat@acme.example.com"))
    assert _membership(db_session, "pat@acme.example.com").pending_approval_at is not None

    s["tenant"].approve_customer_users = True
    s["customer"].approve_users = False
    db_session.commit()
    _invite(client, _login(client, s["tenant"], s["admin"].email), s, "sam@acme.example.com")
    _accept(client, _link(db_session, "sam@acme.example.com"))
    assert _membership(db_session, "sam@acme.example.com").is_active


def test_the_banks_setting_and_people_it_adds_itself(client, db_session, tenant_factory):
    from openclear.services import user_service

    s = _setup(db_session, tenant_factory, slug="ap-bank")
    csrf = _login(client, s["tenant"], s["users"]["admin"].email)
    assert "New customer users" in client.get("/ui/settings").text
    client.post("/ui/settings/customer-approval", data={"csrf_token": csrf, "approve_customer_users": "true"})
    db_session.expire_all()
    assert s["tenant"].approve_customer_users is True
    # The bank adding someone to a customer itself: no waiting.
    user_service.create_user_with_membership(db_session, s["tenant"].id, email="direct@acme.example.com", password=TenantFactory.PASSWORD,
                                             security_group_id=s["clerk"].id, customer_id=s["customer"].id)
    db_session.commit()
    assert _membership(db_session, "direct@acme.example.com").is_active
    # A customer's session never reaches the approval page.
    _login(client, s["tenant"], s["admin"].email)
    assert client.get("/ui/approvals/customer-users").status_code == 403


def test_the_customers_own_sign_on_creates_people_who_wait(client, db_session, tenant_factory, monkeypatch):
    s = _setup(db_session, tenant_factory, slug="ap-sso")
    s["admin_role"].permissions = [*s["admin_role"].permissions, "customer_sso:manage"]
    s["customer"].self_manages_sso = True
    s["tenant"].approve_customer_users = True
    db_session.commit()
    csrf = _login(client, s["tenant"], s["admin"].email)
    connection = _connection(client, csrf, s, db_session)
    client.post(f"/ui/sign-on/{connection.id}/mappings", data={"csrf_token": csrf, "external_group": "acme-staff",
                                                               "security_group_id": str(s["clerk"].id)})
    db_session.add(CustomerEmailDomain(tenant_id=s["tenant"].id, customer_id=s["customer"].id, domain="acme.example.com",
                                       verification_token="t", verified_at=datetime.now(timezone.utc)))
    db_session.commit()

    client.cookies.clear()
    first = _sign_in(client, connection, s["tenant"], monkeypatch, "new@acme.example.com")
    assert first.status_code != 303 and "Your bank approves new people" in first.text
    membership = _membership(db_session, "new@acme.example.com")
    assert membership is not None and not membership.is_active and membership.pending_approval_at is not None
    client.cookies.clear()
    again = _sign_in(client, connection, s["tenant"], monkeypatch, "new@acme.example.com")  # signing in again doesn't let them in
    assert again.status_code != 303 and not _membership(db_session, "new@acme.example.com").is_active

    csrf = _login(client, s["tenant"], s["users"]["admin"].email)
    client.post(f"/ui/approvals/customer-users/{membership.id}/approve", data={"csrf_token": csrf})
    client.cookies.clear()
    approved = _sign_in(client, connection, s["tenant"], monkeypatch, "new@acme.example.com")
    assert approved.status_code == 303 and approved.headers["location"] == "/ui/"
