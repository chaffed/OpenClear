# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

from openclear.domain.notification import NotificationType
from openclear.services import notification_service
from tests.conftest import TenantFactory


def _login(client, tenant_slug, email, password=TenantFactory.PASSWORD):
    client.get("/ui/login")
    csrf = client.cookies.get("csrf_token")
    client.post(
        "/ui/login", data={"tenant_slug": tenant_slug, "email": email, "password": password, "csrf_token": csrf, "next": "/ui/"}
    )
    return csrf


def test_notification_settings_page_renders(client, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="web-notif-render")
    _login(client, tenant.slug, users["admin"].email)

    resp = client.get("/ui/security/notifications")
    assert resp.status_code == 200
    assert "New exception ready for review" in resp.text
    assert "A recommendation is awaiting your approval" in resp.text
    assert "Your account was unlocked" in resp.text
    # ACCOUNT_LOCKED is not preference-controlled and must not appear as a toggle row
    assert "email_account_locked" not in resp.text


def test_setting_phone_number_persists(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="web-notif-phone")
    csrf = _login(client, tenant.slug, users["admin"].email)

    resp = client.post(
        "/ui/security/notifications/phone", data={"csrf_token": csrf, "phone": "+15559876543"}, follow_redirects=False
    )
    assert resp.status_code == 303

    page = client.get("/ui/security/notifications")
    assert "+15559876543" in page.text


def test_clearing_phone_number_disables_sms_checkboxes(client, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="web-notif-phone-clear")
    csrf = _login(client, tenant.slug, users["admin"].email)

    client.post("/ui/security/notifications/phone", data={"csrf_token": csrf, "phone": "+15551110000"})
    page_with_phone = client.get("/ui/security/notifications")
    assert 'name="sms_exception_created" ' in page_with_phone.text
    assert "disabled" not in page_with_phone.text.split('name="sms_exception_created"')[1].split(">")[0]

    client.post("/ui/security/notifications/phone", data={"csrf_token": csrf, "phone": ""})
    page_without_phone = client.get("/ui/security/notifications")
    assert "disabled" in page_without_phone.text.split('name="sms_exception_created"')[1].split(">")[0]


def test_saving_preferences_persists_across_types(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="web-notif-save-prefs")
    csrf = _login(client, tenant.slug, users["admin"].email)

    resp = client.post(
        "/ui/security/notifications",
        data={
            "csrf_token": csrf,
            "email_exception_created": "on",
            # recommendation_awaiting_approval's email deliberately left unchecked
            "email_account_unlocked": "on",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303

    prefs = notification_service.get_preferences(db_session, users["admin"].id)
    assert prefs[NotificationType.EXCEPTION_CREATED].email_enabled is True
    assert prefs[NotificationType.RECOMMENDATION_AWAITING_APPROVAL].email_enabled is False
    assert prefs[NotificationType.ACCOUNT_UNLOCKED].email_enabled is True


def test_notification_settings_requires_login(client):
    resp = client.get("/ui/security/notifications", follow_redirects=False)
    assert resp.status_code in (302, 303)
