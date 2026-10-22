# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""A customer's *Default decision* page (SIMPLIFY_PLAN.md SP-D7): what happens to its
exceptions if no one decides them by the cut-off. Customers have no models of their own."""

from openclear.services import account_service, customer_service, security_group_service, user_service
from tests.conftest import TenantFactory

PAGE = "/ui/admin/customers/{}/default-decision"


def _login(client, tenant_slug, email, password=TenantFactory.PASSWORD):
    client.get("/ui/login")
    csrf = client.cookies.get("csrf_token")
    return client.post(
        "/ui/login", data={"tenant_slug": tenant_slug, "email": email, "password": password, "csrf_token": csrf, "next": "/ui/"}
    )


def _make_customer(db_session, tenant, customer_number="C-1"):
    customer = customer_service.create_customer(
        db_session, tenant.id, customer_service.CustomerInput(customer_number=customer_number, name="Acme Co")
    )
    account = account_service.create_account(
        db_session, tenant.id, account_service.AccountInput(account_number=f"{customer_number}-ACCT", name="Acct", customer_id=customer.id)
    )
    db_session.commit()
    return customer, account


def _csrf(client):
    return client.cookies.get("csrf_token")


def test_default_decision_page_requires_admin_permission(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="web-cust-dd-forbidden")
    customer, _account2 = _make_customer(db_session, tenant)
    _login(client, tenant.slug, users["preparer"].email)

    resp = client.get(PAGE.format(customer.id), follow_redirects=False)
    assert resp.status_code == 403


def test_default_decision_page_shows_each_network(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="web-cust-dd-show")
    customer, _account2 = _make_customer(db_session, tenant)
    _login(client, tenant.slug, users["admin"].email)

    resp = client.get(PAGE.format(customer.id))
    assert resp.status_code == 200
    assert "Default decision" in resp.text
    assert "check" in resp.text and "ach" in resp.text
    assert "model" not in resp.text.lower().split("<main", 1)[-1]  # no model or scoring cards


def test_the_old_ml_page_redirects_to_the_default_decision(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="web-cust-dd-moved")
    customer, _account2 = _make_customer(db_session, tenant)
    _login(client, tenant.slug, users["admin"].email)

    resp = client.get(f"/ui/admin/customers/{customer.id}/ml", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"].endswith(PAGE.format(customer.id))


def test_default_decision_page_404s_for_customer_in_another_tenant(client, db_session, tenant_factory):
    tenant_a, _account_a, users_a = tenant_factory.make(slug="web-cust-dd-xtenant-a")
    tenant_b, _account_b, _users_b = tenant_factory.make(slug="web-cust-dd-xtenant-b")
    customer_b, _account_b2 = _make_customer(db_session, tenant_b, "C-B")
    _login(client, tenant_a.slug, users_a["admin"].email)

    resp = client.get(PAGE.format(customer_b.id), follow_redirects=False)
    assert resp.status_code == 404


def test_the_score_is_never_a_default_decision(client, db_session, tenant_factory):
    """SIMPLIFY_PLAN.md SP-D6: the score is advisory, so "ml_determined" isn't a choice."""
    tenant, _account, users = tenant_factory.make(slug="web-cust-dd-no-ml")
    customer, _account2 = _make_customer(db_session, tenant)
    _login(client, tenant.slug, users["admin"].email)

    assert "ml_determined" not in client.get(PAGE.format(customer.id)).text
    resp = client.post(
        f"/ui/admin/customers/{customer.id}/disposition",
        data={"network_code": "check", "mode": "ml_determined", "csrf_token": _csrf(client)},
        follow_redirects=False,
    )
    assert resp.status_code == 422


def test_set_customer_disposition_fixed_pay(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="web-cust-disp-fixed-pay")
    customer, _account2 = _make_customer(db_session, tenant)
    _login(client, tenant.slug, users["admin"].email)
    client.get(PAGE.format(customer.id))

    resp = client.post(
        f"/ui/admin/customers/{customer.id}/disposition",
        data={"network_code": "check", "mode": "fixed_pay", "csrf_token": _csrf(client)},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert "error" not in resp.headers["location"]

    from openclear.domain.customer_disposition_setting import DispositionMode
    from openclear.services import auto_disposition_service

    db_session.expire_all()
    assert auto_disposition_service.get_disposition_mode(db_session, tenant.id, customer.id, "check") == DispositionMode.FIXED_PAY


def test_set_customer_disposition_with_ach_return_reason(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="web-cust-disp-ach-reason")
    customer, _account2 = _make_customer(db_session, tenant)
    _login(client, tenant.slug, users["admin"].email)
    client.get(PAGE.format(customer.id))

    from openclear.services import ach_return_reason_service, auto_disposition_service

    reason = ach_return_reason_service.list_ach_return_reasons(db_session, tenant.id, active_only=True)[0]

    resp = client.post(
        f"/ui/admin/customers/{customer.id}/disposition",
        data={
            "network_code": "ach", "mode": "fixed_return", "response_window_hours": "",
            "default_ach_return_reason_id": str(reason.id), "csrf_token": _csrf(client),
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert "error" not in resp.headers["location"]

    db_session.expire_all()
    summary = {s.network_code: s for s in auto_disposition_service.get_disposition_summary(db_session, tenant.id, customer.id)}
    assert summary["ach"].default_ach_return_reason_id == reason.id


def test_paying_by_default_waits_for_a_second_admin(client, db_session, tenant_factory):
    """FULL_REVIEW_PLAN.md FR4: "always pay" pays what nobody looked at, so under the bank's
    second-admin rule it waits, as the cut-off beside it does; leaving items open doesn't."""
    from openclear.domain.customer_disposition_setting import DispositionMode
    from openclear.domain.tenant import Tenant
    from openclear.services import auto_disposition_service
    from tests.test_auth.test_dual_control import _second_admin

    tenant, _account, users = tenant_factory.make(slug="web-cust-disp-protected")
    customer, _account2 = _make_customer(db_session, tenant)
    _second_admin(db_session, tenant)
    db_session.get(Tenant, tenant.id).sensitive_changes_need_approval = True
    db_session.commit()
    _login(client, tenant.slug, users["admin"].email)
    client.get(PAGE.format(customer.id))

    def post(mode):
        return client.post(f"/ui/admin/customers/{customer.id}/disposition",
                           data={"network_code": "check", "mode": mode, "csrf_token": _csrf(client)}, follow_redirects=False)

    assert "approv" in post("fixed_pay").headers["location"].lower()
    db_session.expire_all()
    assert auto_disposition_service.get_disposition_mode(db_session, tenant.id, customer.id, "check") == DispositionMode.NONE
    from openclear.domain.change_request import ChangeRequest
    from sqlalchemy import select as _select

    waiting = db_session.execute(_select(ChangeRequest).where(ChangeRequest.kind == "customer.disposition")).scalar_one()
    assert waiting.status == "pending"


def test_customer_detail_page_shows_the_default_decision_card_for_admin_only(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="web-cust-dd-card")
    customer, _account2 = _make_customer(db_session, tenant)

    customer_manage_only_group = security_group_service.create_security_group(
        db_session, tenant.id, security_group_service.SecurityGroupInput(name="Customer Manager", permissions=["customer:manage"])
    )
    limited_user = user_service.create_user_with_membership(
        db_session, tenant.id, email="custmgr@web-cust-dd-card.example.com", password=TenantFactory.PASSWORD,
        security_group_id=customer_manage_only_group.id,
    )
    db_session.commit()

    _login(client, tenant.slug, users["admin"].email)
    admin_resp = client.get(f"/ui/customers/{customer.id}")
    assert PAGE.format(customer.id) in admin_resp.text
    assert "ML scoring" not in admin_resp.text

    client.cookies.clear()
    _login(client, tenant.slug, limited_user.email)
    limited_resp = client.get(f"/ui/customers/{customer.id}")
    assert PAGE.format(customer.id) not in limited_resp.text
