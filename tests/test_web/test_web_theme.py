# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

from tests.conftest import TenantFactory


def _login(client, tenant_slug, email, password=TenantFactory.PASSWORD):
    client.get("/ui/login")
    csrf = client.cookies.get("csrf_token")
    client.post(
        "/ui/login", data={"tenant_slug": tenant_slug, "email": email, "password": password, "csrf_token": csrf, "next": "/ui/"}
    )
    return csrf


def test_theme_defaults_to_system_no_data_theme_attribute(client, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="theme-default")
    _login(client, tenant.slug, users["admin"].email)

    resp = client.get("/ui/")
    assert 'data-theme="dark"' not in resp.text
    assert 'data-theme="light"' not in resp.text


def test_setting_dark_theme_persists_via_cookie(client, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="theme-dark")
    csrf = _login(client, tenant.slug, users["admin"].email)

    resp = client.post("/ui/theme", data={"theme": "dark", "csrf_token": csrf, "next": "/ui/"}, follow_redirects=False)
    assert resp.status_code == 303
    assert client.cookies.get("openclear_theme") == "dark"

    dashboard = client.get("/ui/")
    assert 'data-theme="dark"' in dashboard.text


def test_setting_light_theme_overrides_and_system_clears_cookie(client, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="theme-light-then-system")
    csrf = _login(client, tenant.slug, users["admin"].email)

    client.post("/ui/theme", data={"theme": "light", "csrf_token": csrf, "next": "/ui/"})
    assert client.cookies.get("openclear_theme") == "light"
    assert 'data-theme="light"' in client.get("/ui/").text

    client.post("/ui/theme", data={"theme": "system", "csrf_token": csrf, "next": "/ui/"})
    assert client.cookies.get("openclear_theme") is None
    resp = client.get("/ui/")
    assert 'data-theme="light"' not in resp.text
    assert 'data-theme="dark"' not in resp.text


def test_theme_toggle_works_pre_login(client):
    login_page = client.get("/ui/login")
    csrf = client.cookies.get("csrf_token")
    resp = client.post("/ui/theme", data={"theme": "dark", "csrf_token": csrf, "next": "/ui/login"}, follow_redirects=False)
    assert resp.status_code == 303
    assert client.cookies.get("openclear_theme") == "dark"

    login_again = client.get("/ui/login")
    assert 'data-theme="dark"' in login_again.text


def test_theme_requires_csrf_token(client, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="theme-no-csrf")
    _login(client, tenant.slug, users["admin"].email)

    resp = client.post("/ui/theme", data={"theme": "dark", "csrf_token": "wrong-token", "next": "/ui/"})
    assert resp.status_code == 403
