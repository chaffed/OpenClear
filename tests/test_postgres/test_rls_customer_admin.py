# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Customers managing their own people, and the "belongs anywhere else?" check behind every
password reset, with Postgres row-level security enforced (the app runs as a restricted
role). Under RLS an admin's session sees only its own bank's memberships, so without the
probe (db/tenancy.py::memberships_elsewhere) everyone would look like they belong nowhere
else. Runs only with OPENCLEAR_TEST_DATABASE_URL set."""

import re

import pytest
from sqlalchemy import select

from openclear.domain.notification import Notification, NotificationType
from openclear.services import security_group_service, user_service
from tests.conftest import TEST_DATABASE_URL
from tests.test_postgres.test_rls_background_and_switching import _web_login
from tests.test_web.test_customer_people import _membership, _setup

pytestmark = pytest.mark.skipif(not TEST_DATABASE_URL, reason="needs OPENCLEAR_TEST_DATABASE_URL (Postgres)")


def test_a_bank_cannot_reset_the_password_of_someone_at_another_bank(rls_client, db_session, tenant_factory):
    tenant_a, _account_a, users_a = tenant_factory.make(slug="rls-reset-a")
    tenant_b, _account_b, users_b = tenant_factory.make(slug="rls-reset-b")
    shared = users_b["viewer"]
    user_service.confirm_cross_tenant_membership(
        db_session, tenant_a.id, email=shared.email,
        security_group_id=security_group_service.get_security_group_by_name(db_session, tenant_a.id, "Viewer").id)
    db_session.commit()
    membership = next(m for m in user_service.list_memberships_for_user(db_session, shared.id) if m.tenant.id == tenant_a.id).membership

    csrf = _web_login(rls_client, tenant_a.slug, users_a["admin"].email)
    refused = rls_client.post(f"/ui/users/{membership.id}/reset-password", data={"csrf_token": csrf})
    assert "also belongs to other organizations" in refused.text


def test_a_customer_administrator_under_rls(rls_client, db_session, tenant_factory):
    s = _setup(db_session, tenant_factory, slug="rls-cp")
    shared = s["users"]["preparer"]  # the bank's own staff, also at this customer
    user_service.add_user(db_session, s["tenant"].id, email=shared.email, password="", security_group_id=s["clerk"].id,
                          customer_id=s["customer"].id)
    db_session.commit()
    shared_m = _membership(db_session, s["tenant"], shared, s["customer"])

    csrf = _web_login(rls_client, s["tenant"].slug, s["admin"].email)
    assert rls_client.get("/ui/people").status_code == 200
    refused = rls_client.post(f"/ui/people/{shared_m.id}/reset-password", data={"csrf_token": csrf})
    assert refused.status_code == 422 and "another organization" in refused.text

    sent = rls_client.post("/ui/people/invite", data={"csrf_token": csrf, "email": "new@acme.example.com",
                                                      "security_group_id": str(s["clerk"].id)}, follow_redirects=False)
    assert sent.status_code == 303
    db_session.expire_all()
    note = db_session.execute(select(Notification).where(Notification.notification_type == NotificationType.INVITATION)).scalars().one()
    link = re.search(r"(/ui/invitations/\S+)", note.body).group(1)
    rls_client.cookies.clear()
    assert "Choose a password" in rls_client.get(link).text
    csrf = rls_client.cookies.get("csrf_token")
    accepted = rls_client.post(link, data={"csrf_token": csrf, "password": "A-much-better-passw0rd!",
                                           "password_confirm": "A-much-better-passw0rd!"}, follow_redirects=False)
    assert accepted.status_code == 303, accepted.text[:300]
    rls_client.cookies.clear()
    rls_client.get("/ui/login")
    signed_in = rls_client.post("/ui/login", data={"tenant_slug": s["tenant"].slug, "email": "new@acme.example.com",
                                                   "password": "A-much-better-passw0rd!", "csrf_token": rls_client.cookies.get("csrf_token"),
                                                   "next": "/ui/"}, follow_redirects=False)
    assert signed_in.status_code == 303
