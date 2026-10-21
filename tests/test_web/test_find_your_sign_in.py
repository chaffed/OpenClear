# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Finding one's sign-in by email (FULL_REVIEW_PLAN.md FR8, U2): the links go to the inbox, and
the page answers the same whether the address has a sign-in or not."""

import pytest
from sqlalchemy import select

from openclear.domain.customer import Customer
from openclear.domain.notification import Notification, NotificationType
from openclear.services import security_group_service


def _ask(client, email):
    client.get("/ui/login/find")
    return client.post("/ui/login/find", data={"email": email, "csrf_token": client.cookies.get("csrf_token")})


def _links_sent(db_session, user) -> list[str]:
    return [n.body for n in db_session.execute(select(Notification).where(
        Notification.recipient_user_id == user.id, Notification.notification_type == NotificationType.SIGN_IN_LINKS)).scalars()]


@pytest.mark.asvs("6.3.8")
def test_the_page_answers_the_same_for_any_address(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="find-same")
    known = _ask(client, users["viewer"].email)
    unknown = _ask(client, "nobody-at-all@example.com")
    assert known.status_code == unknown.status_code == 200
    assert known.text.replace(users["viewer"].email, "X") == unknown.text.replace("nobody-at-all@example.com", "X")


def test_the_links_cover_every_organization_the_person_belongs_to(client, db_session, tenant_factory):
    bank, _account, users = tenant_factory.make(name="First Bank", slug="find-first")
    other, _other_account, _other_users = tenant_factory.make(name="Second Bank", slug="find-second")
    customer = Customer(tenant_id=other.id, name="Maple Street Dental", customer_number="MSD 1")
    db_session.add(customer)
    db_session.flush()
    from openclear.domain.tenant_membership import TenantMembership

    group = security_group_service.get_security_group_by_name(db_session, other.id, "Viewer")
    db_session.add(TenantMembership(user_id=users["viewer"].id, tenant_id=other.id, customer_id=customer.id, security_group_id=group.id))
    db_session.commit()

    assert _ask(client, users["viewer"].email.upper()).status_code == 200  # any capitals
    bodies = _links_sent(db_session, users["viewer"])
    assert len(bodies) == 1
    assert "/ui/login/find-first" in bodies[0] and "First Bank" in bodies[0]
    assert "/ui/login/find-second/MSD%201" in bodies[0] and "Maple Street Dental at Second Bank" in bodies[0]


def test_the_plain_sign_in_page_offers_it(client):
    assert 'href="/ui/login/find"' in client.get("/ui/login").text
