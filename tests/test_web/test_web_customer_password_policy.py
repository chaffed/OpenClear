# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

from openclear.services import customer_service, tenant_service
from tests.conftest import TenantFactory


def _login(client, tenant_slug, email, password=TenantFactory.PASSWORD):
    client.get("/ui/login")
    csrf = client.cookies.get("csrf_token")
    client.post(
        "/ui/login", data={"tenant_slug": tenant_slug, "email": email, "password": password, "csrf_token": csrf, "next": "/ui/"}
    )
    return csrf


def _make_customer(db_session, tenant, customer_number="C-1"):
    customer = customer_service.create_customer(
        db_session, tenant.id, customer_service.CustomerInput(customer_number=customer_number, name="Acme Co")
    )
    db_session.commit()
    return customer


def test_password_policy_page_requires_customer_manage(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="cust-pw-policy-forbidden")
    customer = _make_customer(db_session, tenant)
    _login(client, tenant.slug, users["preparer"].email)

    resp = client.get(f"/ui/customers/{customer.id}/password-policy", follow_redirects=False)
    assert resp.status_code == 403


def test_password_policy_page_shows_tenant_baseline(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="cust-pw-policy-baseline")
    tenant_service.set_password_policy(
        db_session, tenant.id, min_length=10, require_uppercase=True, require_lowercase=False,
        require_number=False, require_symbol=False,
    )
    customer = _make_customer(db_session, tenant)
    _login(client, tenant.slug, users["admin"].email)

    resp = client.get(f"/ui/customers/{customer.id}/password-policy")
    assert resp.status_code == 200
    assert "minimum 10 characters" in resp.text
    assert "an uppercase letter" in resp.text
    # already mandated by the tenant -- can't be unchecked here
    assert "checked disabled" in resp.text


def test_set_customer_password_policy_via_web(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="cust-pw-policy-set")
    customer = _make_customer(db_session, tenant)
    csrf = _login(client, tenant.slug, users["admin"].email)

    resp = client.post(
        f"/ui/customers/{customer.id}/password-policy",
        data={"csrf_token": csrf, "min_length": "16", "require_symbol": "true"},
        follow_redirects=False,
    )
    assert resp.status_code == 303

    db_session.expire_all()
    updated = customer_service.get_customer(db_session, tenant.id, customer.id)
    assert updated.password_min_length == 16
    assert updated.password_require_symbol is True


def test_set_customer_password_policy_rejects_weaker_than_tenant(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="cust-pw-policy-weaker")
    tenant_service.set_password_policy(
        db_session, tenant.id, min_length=14, require_uppercase=False, require_lowercase=False,
        require_number=False, require_symbol=False,
    )
    customer = _make_customer(db_session, tenant)
    csrf = _login(client, tenant.slug, users["admin"].email)

    resp = client.post(
        f"/ui/customers/{customer.id}/password-policy",
        data={"csrf_token": csrf, "min_length": "6"},
    )
    assert resp.status_code == 422
    assert "can&#39;t be less than the tenant&#39;s own minimum" in resp.text or "can't be less than the tenant's own minimum" in resp.text

    db_session.expire_all()
    unchanged = customer_service.get_customer(db_session, tenant.id, customer.id)
    assert unchanged.password_min_length is None


def test_customer_detail_page_links_to_password_policy(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="cust-pw-policy-link")
    customer = _make_customer(db_session, tenant)
    _login(client, tenant.slug, users["admin"].email)

    resp = client.get(f"/ui/customers/{customer.id}")
    assert f'href="/ui/customers/{customer.id}/password-policy"' in resp.text
