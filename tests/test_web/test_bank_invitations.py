# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed
"""A bank invites one of a customer's people with no access (UI_PLAN.md UI-D14): they can sign
in and look after their own sign-in, nothing else, until someone other than whoever invited
them gives them a role. An invitation never grants access on its own."""

import re

from sqlalchemy import select

from openclear.domain.notification import Notification, NotificationType
from openclear.domain.security_group import NO_ACCESS, SecurityGroup
from openclear.domain.tenant_membership import TenantMembership
from openclear.domain.user import User
from openclear.services import security_group_service, user_service
from tests.conftest import TenantFactory
from tests.test_ml.test_per_customer_ml import _make_customer_with_account
from tests.test_web.test_journeys import _sign_in_as

PASSWORD = "A-much-better-passw0rd!"


def _link(db_session, email):
    db_session.expire_all()
    note = db_session.execute(select(Notification).where(
        Notification.notification_type == NotificationType.INVITATION, Notification.destination == email)).scalars().first()
    return re.search(r"(/ui/invitations/\S+)", note.body).group(1)


def _invite_and_join(client, db_session, tenant, users, customer, email):
    _sign_in_as(client, tenant, users["admin"].email)
    csrf = client.cookies.get("csrf_token")
    sent = client.post(f"/ui/customers/{customer.id}/invite", data={"csrf_token": csrf, "email": email}, follow_redirects=True)
    assert "Invitation sent" in sent.text
    link = _link(db_session, email)
    client.cookies.clear()
    client.get(link)
    accepted = client.post(link, data={"csrf_token": client.cookies.get("csrf_token"), "password": PASSWORD, "password_confirm": PASSWORD},
                           follow_redirects=False)
    assert accepted.status_code == 303, accepted.text
    db_session.expire_all()
    user = db_session.execute(select(User).where(User.email == email)).scalar_one()
    return db_session.execute(select(TenantMembership).where(TenantMembership.user_id == user.id)).scalar_one()


def test_an_invited_person_can_only_look_after_their_own_sign_in(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="bank-invite")
    customer, _cust_account = _make_customer_with_account(db_session, tenant, "INV-1")
    assert not customer.self_manages_users  # the bank invites even where the customer doesn't manage its people
    membership = _invite_and_join(client, db_session, tenant, users, customer, "newcomer@inv-customer.example.com")
    assert db_session.get(SecurityGroup, membership.security_group_id).system_role == NO_ACCESS

    client.cookies.clear()
    client.get("/ui/login")
    client.post("/ui/login", data={"tenant_slug": tenant.slug, "email": "newcomer@inv-customer.example.com", "password": PASSWORD,
                                   "csrf_token": client.cookies.get("csrf_token"), "next": "/ui/"})
    home = client.get("/ui/").text
    assert "you don&#39;t have access to anything yet" in home or "you don't have access to anything yet" in home
    assert 'href="/ui/accounts"' not in home and 'href="/ui/exceptions"' not in home
    assert client.get("/ui/security").status_code == 200
    assert client.get("/ui/exceptions").status_code == 403


def test_someone_other_than_the_inviter_gives_the_role(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="bank-invite-grant")
    customer, _cust_account = _make_customer_with_account(db_session, tenant, "INV-2")
    membership = _invite_and_join(client, db_session, tenant, users, customer, "pat@inv2-customer.example.com")
    viewer = security_group_service.get_security_group_by_name(db_session, tenant.id, "Viewer")
    admin = security_group_service.get_security_group_by_name(db_session, tenant.id, "Admin")

    _sign_in_as(client, tenant, users["admin"].email)  # the inviter
    refused = client.post(f"/ui/users/{membership.id}", data={"csrf_token": client.cookies.get("csrf_token"),
                                                              "security_group_id": str(viewer.id), "customer_id": str(customer.id)})
    assert refused.status_code == 422 and "someone else gives them a role" in refused.text
    db_session.expire_all()
    assert db_session.get(TenantMembership, membership.id).security_group_id != viewer.id

    other = user_service.create_user_with_membership(db_session, tenant.id, email="second-admin@inv2-bank.example.com",
                                                     password=TenantFactory.PASSWORD, security_group_id=admin.id)
    db_session.commit()
    _sign_in_as(client, tenant, other.email)
    client.post(f"/ui/users/{membership.id}", data={"csrf_token": client.cookies.get("csrf_token"),
                                                    "security_group_id": str(viewer.id), "customer_id": str(customer.id)})
    db_session.expire_all()
    assert db_session.get(TenantMembership, membership.id).security_group_id == viewer.id


def test_the_built_in_group_cant_be_given_permissions(db_session, tenant_factory):
    tenant, _account, _users = tenant_factory.make(slug="bank-invite-lock")
    group = security_group_service.no_access_group(db_session, tenant.id)
    try:
        security_group_service.update_security_group(db_session, tenant.id, group.id, security_group_service.SecurityGroupInput(
            name="Sneaky", permissions=["exception:decide"]))
    except ValueError as exc:
        assert "built in" in str(exc)
    else:
        raise AssertionError("the built-in group was changed")
    assert group.permissions == []
