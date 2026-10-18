# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

from openclear.config import get_settings
from tests.conftest import TenantFactory


def test_login_page_renders(client):
    resp = client.get("/ui/login")
    assert resp.status_code == 200
    assert "Log in" in resp.text or "csrf_token" in resp.text


def test_dashboard_redirects_to_login_when_not_authenticated(client):
    resp = client.get("/ui/", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"].startswith("/ui/login")


def test_login_success_sets_cookies_and_redirects_to_dashboard(client, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="web-login-ok")

    csrf = client.get("/ui/login").cookies.get("csrf_token")
    resp = client.post(
        "/ui/login",
        data={
            "tenant_slug": tenant.slug,
            "email": users["admin"].email,
            "password": TenantFactory.PASSWORD,
            "csrf_token": csrf,
            "next": "/ui/",
        },
        follow_redirects=False,
    )

    assert resp.status_code == 303
    assert resp.headers["location"] == "/ui/"
    assert "access_token" in resp.cookies
    assert "refresh_token" in resp.cookies

    dashboard = client.get("/ui/")
    assert dashboard.status_code == 200
    assert "admin" in dashboard.text


def test_login_success_records_last_login_at(client, db_session, tenant_factory):
    from openclear.repositories.user_repo import UserRepository

    tenant, _account, users = tenant_factory.make(slug="web-login-last-login")
    assert UserRepository(db_session).get(users["admin"].id).last_login_at is None

    csrf = client.get("/ui/login").cookies.get("csrf_token")
    client.post(
        "/ui/login",
        data={
            "tenant_slug": tenant.slug,
            "email": users["admin"].email,
            "password": TenantFactory.PASSWORD,
            "csrf_token": csrf,
            "next": "/ui/",
        },
    )

    db_session.expire_all()
    assert UserRepository(db_session).get(users["admin"].id).last_login_at is not None


def test_login_wrong_password_rerenders_form_with_error(client, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="web-login-bad-pw")
    csrf = client.get("/ui/login").cookies.get("csrf_token")

    resp = client.post(
        "/ui/login",
        data={
            "tenant_slug": tenant.slug,
            "email": users["admin"].email,
            "password": "wrong-password",
            "csrf_token": csrf,
            "next": "/ui/",
        },
    )

    assert resp.status_code == 401
    assert "Invalid" in resp.text


def test_login_locks_after_max_failed_attempts_and_blocks_correct_password(client, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="web-login-lockout")
    max_attempts = get_settings().login_max_failed_attempts

    csrf = client.get("/ui/login").cookies.get("csrf_token")
    for _ in range(max_attempts):
        resp = client.post(
            "/ui/login",
            data={
                "tenant_slug": tenant.slug,
                "email": users["admin"].email,
                "password": "wrong-password",
                "csrf_token": csrf,
                "next": "/ui/",
            },
        )
    assert resp.status_code == 401
    assert "an account is locked" in resp.text.lower()  # the same answer as a wrong password (ASVS 6.3.8)

    resp = client.post(
        "/ui/login",
        data={
            "tenant_slug": tenant.slug,
            "email": users["admin"].email,
            "password": TenantFactory.PASSWORD,
            "csrf_token": csrf,
            "next": "/ui/",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 401
    assert "an account is locked" in resp.text.lower()  # the same answer as a wrong password (ASVS 6.3.8)


def test_login_missing_csrf_token_rejected(client, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="web-login-no-csrf")
    client.get("/ui/login")  # establishes a csrf cookie the form submission below ignores

    resp = client.post(
        "/ui/login",
        data={
            "tenant_slug": tenant.slug,
            "email": users["admin"].email,
            "password": TenantFactory.PASSWORD,
            "csrf_token": "not-the-real-token",
            "next": "/ui/",
        },
    )

    assert resp.status_code == 403


def test_open_redirect_via_next_param_is_blocked(client, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="web-open-redirect")
    csrf = client.get("/ui/login").cookies.get("csrf_token")

    resp = client.post(
        "/ui/login",
        data={
            "tenant_slug": tenant.slug,
            "email": users["admin"].email,
            "password": TenantFactory.PASSWORD,
            "csrf_token": csrf,
            "next": "http://evil.example.com/steal",
        },
        follow_redirects=False,
    )

    assert resp.status_code == 303
    assert resp.headers["location"] == "/ui/"  # fell back to the safe default, not the attacker URL


def test_logout_clears_cookies_and_redirects_to_login(client, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="web-logout")
    csrf = client.get("/ui/login").cookies.get("csrf_token")
    client.post(
        "/ui/login",
        data={
            "tenant_slug": tenant.slug,
            "email": users["admin"].email,
            "password": TenantFactory.PASSWORD,
            "csrf_token": csrf,
            "next": "/ui/",
        },
    )
    assert client.get("/ui/").status_code == 200  # confirms we were actually logged in

    dashboard_csrf = client.cookies.get("csrf_token")
    logout_resp = client.post("/ui/logout", data={"csrf_token": dashboard_csrf}, follow_redirects=False)
    assert logout_resp.status_code == 303
    assert logout_resp.headers["location"] == "/ui/login"

    # Cookies must actually be gone, not just that the server said to clear them.
    assert client.cookies.get("access_token") is None
    assert client.cookies.get("refresh_token") is None

    after_logout = client.get("/ui/", follow_redirects=False)
    assert after_logout.status_code == 303
