# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import base64
import io

from PIL import Image

from openclear.services import customer_service, security_group_service, user_service
from tests.conftest import TenantFactory


def _png_data_uri():
    image = Image.new("RGB", (10, 10), color="green")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return f"data:image/png;base64,{base64.b64encode(buffer.getvalue()).decode()}"


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


def _make_customer_scoped_user(db_session, tenant, customer, *, group_name="Admin", email="scoped@customer.example.com"):
    group = security_group_service.get_security_group_by_name(db_session, tenant.id, group_name)
    user = user_service.create_user_with_membership(
        db_session, tenant.id, email=email, password=TenantFactory.PASSWORD, security_group_id=group.id, customer_id=customer.id
    )
    db_session.commit()
    return user


def test_customer_scoped_admin_can_view_and_set_own_banner(client, db_session, tenant_factory):
    tenant, _account, _users = tenant_factory.make(slug="cust-banner-self-service")
    customer = _make_customer(db_session, tenant)
    _make_customer_scoped_user(db_session, tenant, customer)
    csrf = _login(client, tenant.slug, "scoped@customer.example.com")

    get_resp = client.get(f"/ui/customers/{customer.id}/banner")
    assert get_resp.status_code == 200

    post_resp = client.post(
        f"/ui/customers/{customer.id}/banner",
        data={"csrf_token": csrf, "banner_message": "My **own** message"},
        follow_redirects=False,
    )
    assert post_resp.status_code == 303

    db_session.expire_all()
    updated = customer_service.get_customer(db_session, tenant.id, customer.id)
    assert updated.banner_message == "My **own** message"


def test_customer_scoped_user_without_permission_gets_403(client, db_session, tenant_factory):
    tenant, _account, _users = tenant_factory.make(slug="cust-banner-forbidden")
    customer = _make_customer(db_session, tenant)
    # Preparer doesn't hold customer_banner:manage by default -- only Admin does.
    _make_customer_scoped_user(db_session, tenant, customer, group_name="Preparer")
    _login(client, tenant.slug, "scoped@customer.example.com")

    resp = client.get(f"/ui/customers/{customer.id}/banner", follow_redirects=False)
    assert resp.status_code == 403


def test_customer_scoped_user_cannot_reach_another_customers_banner(client, db_session, tenant_factory):
    tenant, _account, _users = tenant_factory.make(slug="cust-banner-cross-customer")
    own_customer = _make_customer(db_session, tenant, customer_number="C-own")
    other_customer = _make_customer(db_session, tenant, customer_number="C-other")
    _make_customer_scoped_user(db_session, tenant, own_customer)
    csrf = _login(client, tenant.slug, "scoped@customer.example.com")

    get_resp = client.get(f"/ui/customers/{other_customer.id}/banner")
    assert get_resp.status_code == 404

    post_resp = client.post(
        f"/ui/customers/{other_customer.id}/banner", data={"csrf_token": csrf, "banner_message": "sneaky"},
    )
    assert post_resp.status_code == 404

    db_session.expire_all()
    unchanged = customer_service.get_customer(db_session, tenant.id, other_customer.id)
    assert unchanged.banner_message is None


def test_bank_wide_admin_can_also_set_a_customers_banner(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="cust-banner-bank-wide")
    customer = _make_customer(db_session, tenant)
    csrf = _login(client, tenant.slug, users["admin"].email)

    resp = client.post(
        f"/ui/customers/{customer.id}/banner",
        data={"csrf_token": csrf, "banner_message": "Set on the customer's behalf"},
        follow_redirects=False,
    )
    assert resp.status_code == 303

    db_session.expire_all()
    updated = customer_service.get_customer(db_session, tenant.id, customer.id)
    assert updated.banner_message == "Set on the customer's behalf"


def test_bank_wide_preparer_lacks_permission(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="cust-banner-bank-wide-forbidden")
    customer = _make_customer(db_session, tenant)
    _login(client, tenant.slug, users["preparer"].email)

    resp = client.get(f"/ui/customers/{customer.id}/banner", follow_redirects=False)
    assert resp.status_code == 403


def test_customer_detail_page_links_to_banner_for_permitted_user(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="cust-banner-link")
    customer = _make_customer(db_session, tenant)
    _login(client, tenant.slug, users["admin"].email)

    resp = client.get(f"/ui/customers/{customer.id}")
    assert f'href="/ui/customers/{customer.id}/banner"' in resp.text


def test_customer_settings_panel_links_the_banner_for_a_self_service_user(client, db_session, tenant_factory):
    tenant, _account, _users = tenant_factory.make(slug="cust-banner-nav")
    customer = _make_customer(db_session, tenant)
    _make_customer_scoped_user(db_session, tenant, customer)
    _login(client, tenant.slug, "scoped@customer.example.com")

    assert 'href="/ui/settings"' in client.get("/ui/").text
    assert f'href="/ui/customers/{customer.id}/banner"' in client.get("/ui/settings").text


def test_customer_can_save_and_see_a_banner_with_an_embedded_image(client, db_session, tenant_factory):
    tenant, _account, _users = tenant_factory.make(slug="cust-banner-image")
    customer = _make_customer(db_session, tenant)
    _make_customer_scoped_user(db_session, tenant, customer)
    csrf = _login(client, tenant.slug, "scoped@customer.example.com")

    resp = client.post(
        f"/ui/customers/{customer.id}/banner",
        data={"csrf_token": csrf, "banner_message": f"Notice: ![logo]({_png_data_uri()})"},
        follow_redirects=False,
    )
    assert resp.status_code == 303

    db_session.expire_all()
    updated = customer_service.get_customer(db_session, tenant.id, customer.id)
    assert "data:image/png;base64," in updated.banner_message

    dashboard = client.get("/ui/")
    assert "data:image/png;base64," in dashboard.text
