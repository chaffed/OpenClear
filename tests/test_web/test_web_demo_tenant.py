# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from openclear.config import get_settings
from openclear.domain.issued_item import IssuedItem
from openclear.domain.user import User
from openclear.services import account_service, demo_tenant_service, issued_item_service
from tests.conftest import TenantFactory


def _login(client, tenant_slug, email, password=TenantFactory.PASSWORD):
    client.get("/ui/login")
    csrf = client.cookies.get("csrf_token")
    return client.post(
        "/ui/login", data={"tenant_slug": tenant_slug, "email": email, "password": password, "csrf_token": csrf, "next": "/ui/"},
        follow_redirects=False,
    )


def _csrf(client):
    return client.cookies.get("csrf_token")


def _configure_demo_password(monkeypatch, password: str = "WebDemoTest123!") -> None:
    monkeypatch.setattr(get_settings(), "demo_tenant_password", password)


def test_login_web_flow_triggers_reset_after_idle_window(client, db_session, monkeypatch):
    password = "WebDemoTest123!"
    _configure_demo_password(monkeypatch, password)
    tenant = demo_tenant_service.ensure_demo_tenant(db_session)

    account = account_service.list_accounts(db_session, tenant.id)[0]
    issued_item_service.create_issued_item(
        db_session, tenant.id,
        issued_item_service.IssuedItemInput(
            account_id=account.id, check_number="WEBSALESDEMO", amount=Decimal("321.00"), payee_name="Vendor",
            issue_date=date(2026, 1, 1),
        ),
        submitted_by_user_id=None,
    )
    db_session.commit()

    admin = db_session.query(User).filter(User.email == demo_tenant_service.DEMO_ADMIN_EMAIL).one()
    admin.last_login_at = datetime.now(timezone.utc) - timedelta(minutes=get_settings().demo_tenant_session_minutes + 5)
    db_session.commit()

    resp = _login(client, tenant.slug, demo_tenant_service.DEMO_ADMIN_EMAIL, password)
    assert resp.status_code == 303

    db_session.expire_all()
    assert db_session.query(IssuedItem).filter(IssuedItem.tenant_id == tenant.id, IssuedItem.check_number == "WEBSALESDEMO").count() == 0


def test_manual_reset_requires_admin_permission(client, db_session, monkeypatch):
    _configure_demo_password(monkeypatch)
    tenant = demo_tenant_service.ensure_demo_tenant(db_session)

    from openclear.services import security_group_service, user_service

    viewer_group = security_group_service.get_security_group_by_name(db_session, tenant.id, "Viewer")
    viewer = user_service.create_user_with_membership(
        db_session, tenant.id, email="viewer@riversidebank.example.com", password="Reads-only-at-the-bank-7",
        security_group_id=viewer_group.id,
    )
    db_session.commit()
    _login(client, tenant.slug, viewer.email, "Reads-only-at-the-bank-7")

    resp = client.post("/ui/admin/demo/reset", data={"csrf_token": _csrf(client)}, follow_redirects=False)
    assert resp.status_code == 403


def test_manual_reset_404s_for_non_demo_tenant(client, tenant_factory, db_session, monkeypatch):
    _configure_demo_password(monkeypatch)
    demo_tenant_service.ensure_demo_tenant(db_session)

    other_tenant, _account, users = tenant_factory.make(slug="not-the-demo-tenant-web")
    _login(client, other_tenant.slug, users["admin"].email)

    resp = client.post("/ui/admin/demo/reset", data={"csrf_token": _csrf(client)}, follow_redirects=False)
    assert resp.status_code == 404


def test_manual_reset_resets_and_redirects_to_login(client, db_session, monkeypatch):
    password = "WebDemoTest123!"
    _configure_demo_password(monkeypatch, password)
    tenant = demo_tenant_service.ensure_demo_tenant(db_session)

    account = account_service.list_accounts(db_session, tenant.id)[0]
    issued_item_service.create_issued_item(
        db_session, tenant.id,
        issued_item_service.IssuedItemInput(
            account_id=account.id, check_number="MANUALRESETMARK", amount=Decimal("42.00"), payee_name="Vendor",
            issue_date=date(2026, 1, 1),
        ),
        submitted_by_user_id=None,
    )
    db_session.commit()

    _login(client, tenant.slug, demo_tenant_service.DEMO_ADMIN_EMAIL, password)
    resp = client.post("/ui/admin/demo/reset", data={"csrf_token": _csrf(client)}, follow_redirects=False)

    assert resp.status_code == 303
    assert resp.headers["location"].startswith("/ui/login")
    assert "access_token" not in resp.cookies

    db_session.expire_all()
    assert db_session.query(IssuedItem).filter(IssuedItem.tenant_id == tenant.id, IssuedItem.check_number == "MANUALRESETMARK").count() == 0


def test_admin_page_shows_reset_button_only_for_demo_tenant(client, db_session, monkeypatch, tenant_factory):
    _configure_demo_password(monkeypatch)
    demo_tenant = demo_tenant_service.ensure_demo_tenant(db_session)

    _login(client, demo_tenant.slug, demo_tenant_service.DEMO_ADMIN_EMAIL, get_settings().demo_tenant_password)
    demo_admin_page = client.get("/ui/admin")
    assert "Reset demo data now" in demo_admin_page.text

    client.cookies.clear()
    other_tenant, _account, users = tenant_factory.make(slug="regular-tenant-no-reset-button")
    _login(client, other_tenant.slug, users["admin"].email)
    regular_admin_page = client.get("/ui/admin")
    assert "Reset demo data now" not in regular_admin_page.text
