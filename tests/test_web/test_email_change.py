# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed
"""Changing the email someone signs in with (OWASP_PLAN.md OW-D31 to OW-D33; ADR 0006): by the
person or an administrator, confirmed by a link to the new address; by the directory and by
sign-on when the new domain is verified; and everyone at once after a rename."""

import re
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from openclear.domain.audit_log_entry import AuditLogEntry
from openclear.domain.customer_admin import CustomerEmailDomain
from openclear.domain.notification import Notification, NotificationType
from openclear.domain.tenant_membership import TenantMembership
from openclear.domain.user import User
from openclear.services import change_request_service, sso_service
from tests.conftest import TenantFactory


def _sign_in(client, tenant, email):
    client.get("/ui/login")
    client.post("/ui/login", data={"tenant_slug": tenant.slug, "email": email, "password": TenantFactory.PASSWORD,
                                   "csrf_token": client.cookies.get("csrf_token"), "next": "/ui/"})
    return client.cookies.get("csrf_token")


def _sent(db_session, notification_type, destination):
    db_session.expire_all()
    return db_session.execute(select(Notification).where(Notification.notification_type == notification_type,
                                                         Notification.destination == destination)).scalars().all()


def _link(db_session, destination):
    [confirm] = _sent(db_session, NotificationType.EMAIL_CHANGE_CONFIRM, destination)
    return re.search(r"/ui/email-change/\S+", confirm.body).group(0)


def _verified(db_session, tenant, domain, customer_id=None):
    db_session.add(CustomerEmailDomain(tenant_id=tenant.id, customer_id=customer_id, domain=domain, verification_token="t" * 32,
                                       verified_at=datetime.now(timezone.utc)))
    db_session.commit()


@pytest.mark.asvs("6.3.7")
def test_a_person_changes_their_own_email_by_a_link_to_the_new_one(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="email-self")
    viewer = users["viewer"]
    old = viewer.email
    csrf = _sign_in(client, tenant, old)

    client.post("/ui/security/email", data={"new_email": "Viewer.New@Example.org", "csrf_token": csrf})
    link = _link(db_session, "viewer.new@example.org")
    assert db_session.get(User, viewer.id).email == old  # nothing changes until the link is opened

    assert "Confirm your new address" in client.get(link).text
    done = client.post(link, data={"csrf_token": client.cookies.get("csrf_token")})
    assert "Your address has changed" in done.text
    db_session.expire_all()
    assert db_session.get(User, viewer.id).email == "viewer.new@example.org"
    assert _sent(db_session, NotificationType.EMAIL_CHANGED, old) and _sent(db_session, NotificationType.EMAIL_CHANGED, "viewer.new@example.org")
    assert db_session.execute(select(AuditLogEntry).where(AuditLogEntry.action == "user.email.changed",
                                                          AuditLogEntry.resource_id == viewer.id)).scalar_one()
    assert client.post(link, data={"csrf_token": client.cookies.get("csrf_token")}).status_code == 410  # once only


def test_an_address_someone_else_has_is_refused(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="email-taken")
    csrf = _sign_in(client, tenant, users["viewer"].email)
    done = client.post("/ui/security/email", data={"new_email": users["admin"].email, "csrf_token": csrf}, follow_redirects=False)
    assert "Someone%20else" in done.headers["location"]
    assert not _sent(db_session, NotificationType.EMAIL_CHANGE_CONFIRM, users["admin"].email)


def test_an_admin_changes_someone_only_in_their_bank_and_not_someone_elsewhere(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="email-admin")
    other, _a, _u = tenant_factory.make(slug="email-admin-other")
    preparer, viewer = users["preparer"], users["viewer"]
    group = db_session.execute(select(TenantMembership.security_group_id).where(
        TenantMembership.tenant_id == other.id, TenantMembership.user_id == _u["viewer"].id)).scalar_one()
    db_session.add(TenantMembership(tenant_id=other.id, user_id=viewer.id, security_group_id=group))  # the viewer belongs there too
    db_session.commit()
    membership = {u: db_session.execute(select(TenantMembership.id).where(TenantMembership.tenant_id == tenant.id,
                                                                          TenantMembership.user_id == u.id)).scalar_one()
                  for u in (preparer, viewer)}
    csrf = _sign_in(client, tenant, users["admin"].email)

    client.post(f"/ui/users/{membership[preparer]}/email", data={"new_email": "prep@renamed.example.com", "csrf_token": csrf})
    assert "An administrator at" in _sent(db_session, NotificationType.EMAIL_CHANGE_CONFIRM, "prep@renamed.example.com")[0].body
    refused = client.post(f"/ui/users/{membership[viewer]}/email", data={"new_email": "v@renamed.example.com", "csrf_token": csrf},
                          follow_redirects=False)
    assert "other%20organizations" in refused.headers["location"]


def test_the_directory_changes_an_email_and_both_addresses_hear(client, db_session, tenant_factory):
    from tests.test_api.test_scim import _connection, _user_payload

    tenant, _account, _users = tenant_factory.make(slug="email-scim")
    _conn, headers = _connection(db_session, tenant)
    user_id = client.post("/scim/v2/Users", headers=headers, json=_user_payload("pat@acme.example")).json()["id"]
    changed = client.put(f"/scim/v2/Users/{user_id}", headers=headers, json=_user_payload("pat@acme-renamed.example", userName="pat@acme.example"))
    assert changed.status_code == 200, changed.text
    assert _sent(db_session, NotificationType.EMAIL_CHANGED, "pat@acme.example")
    assert _sent(db_session, NotificationType.EMAIL_CHANGED, "pat@acme-renamed.example")


def test_sign_on_moves_a_known_subject_to_a_verified_domain(db_session, tenant_factory):
    from tests.test_web.test_web_sso_login import _claims, _make_mapped_connection

    tenant, _account, _users = tenant_factory.make(slug="email-sso")
    connection, _group = _make_mapped_connection(db_session, tenant)
    assert sso_service.complete_sso_login(db_session, tenant.id, connection, _claims("lee@acme.example.com", "sub-lee")).outcome == "success"
    db_session.commit()
    _verified(db_session, tenant, "acme-renamed.example.com")

    moved = sso_service.complete_sso_login(db_session, tenant.id, connection, _claims("lee@acme-renamed.example.com", "sub-lee"))
    assert moved.outcome == "success" and moved.user.email == "lee@acme-renamed.example.com"
    # An unverified domain is still refused, as before.
    db_session.commit()
    assert sso_service.complete_sso_login(db_session, tenant.id, connection,
                                          _claims("lee@elsewhere.example.com", "sub-lee")).outcome == "identity_conflict"


def test_moving_everyone_to_a_new_domain_needs_a_second_admin(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="email-move")
    second = User(email="second-admin@email-move.example.com", hashed_password=users["admin"].hashed_password)
    db_session.add(second)
    db_session.flush()
    admin_group = db_session.execute(select(TenantMembership.security_group_id).where(
        TenantMembership.tenant_id == tenant.id, TenantMembership.user_id == users["admin"].id)).scalar_one()
    db_session.add(TenantMembership(tenant_id=tenant.id, user_id=second.id, security_group_id=admin_group))
    db_session.commit()
    _verified(db_session, tenant, "renamed.example.com")
    csrf = _sign_in(client, tenant, users["admin"].email)

    asked = client.post("/ui/admin/sso/domains/move", data={"from_domain": "email-move.example.com", "to_domain": "renamed.example.com",
                                                            "csrf_token": csrf}, follow_redirects=False)
    assert "approval" in asked.headers["location"]
    db_session.expire_all()
    assert db_session.get(User, users["viewer"].id).email.endswith("@email-move.example.com")  # not yet

    [change] = change_request_service.pending(db_session, tenant.id)
    change_request_service.decide(db_session, tenant.id, change.id, approve=True, user_id=second.id)
    db_session.commit()
    db_session.expire_all()
    assert db_session.get(User, users["viewer"].id).email == users["viewer"].email.split("@")[0] + "@renamed.example.com"
    assert db_session.get(User, second.id).email == "second-admin@renamed.example.com"


def test_a_move_only_to_a_verified_domain(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="email-move-unverified")
    csrf = _sign_in(client, tenant, users["admin"].email)
    refused = client.post("/ui/admin/sso/domains/move", data={"from_domain": "email-move-unverified.example.com",
                                                              "to_domain": "nope.example.com", "csrf_token": csrf}, follow_redirects=False)
    assert "verified" in refused.headers["location"]
