# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Finding one's sign-in (FULL_REVIEW_PLAN.md FR8) runs signed out, as the app's restricted role:
it must still see the person's memberships in every bank, and a customer's number in another
bank. Runs only with OPENCLEAR_TEST_DATABASE_URL set."""

import pytest
from sqlalchemy import select

from openclear.domain.customer import Customer
from openclear.domain.notification import Notification, NotificationType
from openclear.domain.tenant_membership import TenantMembership
from openclear.services import security_group_service
from tests.conftest import TEST_DATABASE_URL

pytestmark = pytest.mark.skipif(not TEST_DATABASE_URL, reason="needs OPENCLEAR_TEST_DATABASE_URL (Postgres)")


def test_links_found_across_banks_under_rls(rls_client, db_session, tenant_factory):
    bank, _account, users = tenant_factory.make(name="First RLS Bank", slug="rls-find-first")
    other, _other_account, _other_users = tenant_factory.make(name="Second RLS Bank", slug="rls-find-second")
    customer = Customer(tenant_id=other.id, name="Oak Dental", customer_number="OAK-1")
    db_session.add(customer)
    db_session.flush()
    group = security_group_service.get_security_group_by_name(db_session, other.id, "Viewer")
    db_session.add(TenantMembership(user_id=users["viewer"].id, tenant_id=other.id, customer_id=customer.id, security_group_id=group.id))
    db_session.commit()

    rls_client.get("/ui/login/find")
    resp = rls_client.post("/ui/login/find", data={"email": users["viewer"].email, "csrf_token": rls_client.cookies.get("csrf_token")})
    assert resp.status_code == 200
    db_session.expire_all()
    body = db_session.execute(select(Notification.body).where(Notification.recipient_user_id == users["viewer"].id,
                                                              Notification.notification_type == NotificationType.SIGN_IN_LINKS)).scalar_one()
    assert "/ui/login/rls-find-first" in body and "/ui/login/rls-find-second/OAK-1" in body
