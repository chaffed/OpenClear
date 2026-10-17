# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""A customer's own sensitive changes through dual control (DUAL_CONTROL_PLAN.md DC3): approved by
another of its administrators, or by the bank when it has only one."""

from sqlalchemy import select

from openclear.auth import sod
from openclear.domain.customer import Customer
from openclear.domain.tenant_membership import TenantMembership
from openclear.services import change_request_service as changes
from openclear.services import user_service
from tests.conftest import TenantFactory, login_headers
from tests.test_web.test_customer_people import _login, _setup


def _customer_admin(db_session, s, name):
    user = user_service.create_user_with_membership(db_session, s["tenant"].id, email=f"{name}@{s['tenant'].slug}.example.com",
                                                    password=TenantFactory.PASSWORD, security_group_id=s["admin_role"].id,
                                                    customer_id=s["customer"].id)
    db_session.commit()
    return user


def test_a_new_administrator_waits_for_the_bank_when_the_customer_has_one(client, db_session, tenant_factory):
    s = _setup(db_session, tenant_factory, slug="dcc-one")
    s["tenant"].customer_changes_need_approval = True
    db_session.commit()
    clerk = user_service.create_user_with_membership(db_session, s["tenant"].id, email="clerk@dcc-one.example.com", password=TenantFactory.PASSWORD,
                                                     security_group_id=s["clerk"].id, customer_id=s["customer"].id)
    db_session.commit()
    membership = db_session.execute(select(TenantMembership).where(TenantMembership.user_id == clerk.id)).scalar_one()
    csrf = _login(client, s["tenant"], s["admin"].email)
    resp = client.post(f"/ui/people/{membership.id}", data={"csrf_token": csrf, "security_group_id": str(s["admin_role"].id)},
                       follow_redirects=False)
    assert "Sent+for+approval" in resp.headers["location"].replace("%20", "+")
    [change] = changes.pending(db_session, s["tenant"].id, customer_id=s["customer"].id)
    assert change.scope == "customer" and change.applies_at is None  # the bank can approve
    assert not changes.can_decide(db_session, change, s["users"]["viewer"].id)
    bank_admin = s["users"]["admin"]
    assert changes.can_decide(db_session, change, bank_admin.id)  # the customer has nobody else: the bank (customer:manage)
    changes.decide(db_session, s["tenant"].id, change.id, approve=True, user_id=bank_admin.id)
    db_session.commit()
    assert db_session.get(TenantMembership, membership.id).security_group_id == s["admin_role"].id


def test_another_customer_administrator_approves_and_the_bank_then_cant(client, db_session, tenant_factory):
    s = _setup(db_session, tenant_factory, slug="dcc-two")
    second = _customer_admin(db_session, s, "second")
    csrf = _login(client, s["tenant"], s["admin"].email)
    on = client.post("/ui/people/approvals", data={"csrf_token": csrf, "changes_need_approval": "true"}, follow_redirects=False)
    assert "Saved" in on.headers["location"]
    db_session.expire_all()
    assert db_session.get(Customer, s["customer"].id).changes_need_approval is True
    resp = client.post("/ui/people/invite", data={"csrf_token": csrf, "email": "boss@acme.example.com",
                                                  "security_group_id": str(s["admin_role"].id), "scope": "all"}, follow_redirects=False)
    assert "Sent+for+approval" in resp.headers["location"].replace("%20", "+")
    [change] = changes.pending(db_session, s["tenant"].id, customer_id=s["customer"].id)
    assert not changes.can_decide(db_session, change, s["users"]["admin"].id)  # the customer has someone else
    with_api = login_headers(client, s["tenant"].slug, second.email)
    listed = client.get("/api/v1/changes", headers=with_api).json()
    assert [c["kind"] for c in listed] == ["customer.invite_admin"] and listed[0]["can_decide"]
    assert client.post(f"/api/v1/changes/{change.id}/approve", headers=with_api).status_code == 200

    # Turning it off is a protection off: it waits too.
    off = client.post("/ui/people/approvals", data={"csrf_token": csrf}, follow_redirects=False)
    assert "Sent+for+approval" in off.headers["location"].replace("%20", "+")


def test_passwords_back_on_always_need_a_second_person(client, db_session, tenant_factory):
    s = _setup(db_session, tenant_factory, slug="dcc-pw")
    s["customer"].self_manages_sso = True
    s["admin_role"].permissions = [*s["admin_role"].permissions, "customer_sso:manage"]
    db_session.commit()
    csrf = _login(client, s["tenant"], s["admin"].email)
    s["customer"].password_login_enabled = False  # off since signing in (sign-on only from now)
    db_session.commit()
    resp = client.post("/ui/sign-on/password-sign-in", data={"csrf_token": csrf, "allowed": "true"}, follow_redirects=False)
    assert resp.status_code == 303
    db_session.expire_all()
    assert db_session.get(Customer, s["customer"].id).password_login_enabled is False
    [change] = changes.pending(db_session, s["tenant"].id, customer_id=s["customer"].id)
    assert change.kind == "customer.password_sign_in"
    try:
        changes.decide(db_session, s["tenant"].id, change.id, approve=True, user_id=s["admin"].id)
        raise AssertionError("whoever asked can't approve")
    except sod.SodViolation:
        pass
