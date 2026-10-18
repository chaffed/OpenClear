# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Password lifecycle: self-service change (/ui/security/password) and admin reset with a
forced change at next sign-in (/ui/users/{membership_id}/reset-password). See
services/user_service.py::change_own_password / admin_reset_password."""

import re

import pytest
from fastapi.testclient import TestClient

from openclear.auth.password_policy import PasswordPolicy, validate_password
from openclear.domain.audit_log_entry import AuditLogEntry
from openclear.domain.notification import Notification, NotificationChannel, NotificationType
from openclear.domain.tenant_membership import TenantMembership
from openclear.domain.user import User
from openclear.services import customer_service, security_group_service, user_service
from openclear.services.security_group_service import SecurityGroupInput
from tests.conftest import TenantFactory

pytestmark = pytest.mark.usefixtures("email_configured")  # admin password resets email a link

NEW_PASSWORD = "Brand-new-password-42"


def _login(client, tenant_slug, email, password=TenantFactory.PASSWORD):
    client.get("/ui/login")
    return client.post(
        "/ui/login",
        data={"tenant_slug": tenant_slug, "email": email, "password": password,
              "csrf_token": client.cookies.get("csrf_token"), "next": "/ui/"},
        follow_redirects=False,
    )


def _logged_in(resp) -> bool:
    return resp.status_code == 303 and "access_token" in resp.cookies


def _change(client, current, new, confirm=None):
    return client.post(
        "/ui/security/password",
        data={"current_password": current, "new_password": new, "confirm_password": confirm or new,
              "csrf_token": client.cookies.get("csrf_token")},
        follow_redirects=False,
    )


def _membership(db_session, user, tenant):
    return db_session.query(TenantMembership).filter_by(user_id=user.id, tenant_id=tenant.id).one()


def _reset(client, membership_id):
    return client.post(
        f"/ui/users/{membership_id}/reset-password",
        data={"csrf_token": client.cookies.get("csrf_token")},
        follow_redirects=False,
    )


def _reset_link(db_session, user) -> str:
    """The one-time link the reset emailed, as a path."""
    note = db_session.query(Notification).filter_by(recipient_user_id=user.id,
                                                    notification_type=NotificationType.PASSWORD_RESET_LINK).one()
    return re.search(r"(/ui/password-reset/\S+)", note.body).group(1)


def _choose(client, path, password, confirm=None):
    client.get(path)
    return client.post(path, data={"password": password, "password_confirm": confirm or password,
                                   "csrf_token": client.cookies.get("csrf_token")}, follow_redirects=False)


# --- Self-service change ---


def test_user_can_change_own_password(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="pw-self-change")
    viewer = users["viewer"]
    _login(client, tenant.slug, viewer.email)
    assert client.get("/ui/security/password").status_code == 200

    resp = _change(client, TenantFactory.PASSWORD, NEW_PASSWORD)

    assert resp.status_code == 303
    assert "changed" in resp.headers["location"]
    fresh = TestClient(client.app)
    assert not _logged_in(_login(fresh, tenant.slug, viewer.email))
    assert _logged_in(_login(fresh, tenant.slug, viewer.email, NEW_PASSWORD))

    db_session.expire_all()
    email = db_session.query(Notification).filter_by(
        recipient_user_id=viewer.id, notification_type=NotificationType.PASSWORD_CHANGED, channel=NotificationChannel.EMAIL
    ).one()
    assert "changed" in email.body
    assert db_session.query(AuditLogEntry).filter_by(tenant_id=tenant.id, action="user.password_change").count() == 1


def test_password_email_is_sent_even_if_user_opted_out_of_email(client, db_session, tenant_factory):
    from openclear.services import notification_service

    tenant, _account, users = tenant_factory.make(slug="pw-always-email")
    viewer = users["viewer"]
    notification_service.set_preference(
        db_session, viewer.id, NotificationType.PASSWORD_CHANGED, email_enabled=False, sms_enabled=False
    )
    db_session.commit()
    _login(client, tenant.slug, viewer.email)

    _change(client, TenantFactory.PASSWORD, NEW_PASSWORD)

    db_session.expire_all()
    assert db_session.query(Notification).filter_by(
        recipient_user_id=viewer.id, notification_type=NotificationType.PASSWORD_CHANGED, channel=NotificationChannel.EMAIL
    ).count() == 1


def test_password_changed_is_not_an_opt_out_preference(client, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="pw-pref-hidden")
    _login(client, tenant.slug, users["viewer"].email)

    page = client.get("/ui/security/notifications").text

    assert "password_changed" not in page


def test_wrong_current_password_is_rejected_and_counts_toward_lockout(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="pw-wrong-current")
    viewer = users["viewer"]
    _login(client, tenant.slug, viewer.email)

    for _ in range(5):
        resp = _change(client, "not-my-password", NEW_PASSWORD)
        assert resp.status_code == 400
        assert "current password is incorrect" in resp.text

    db_session.expire_all()
    user = db_session.get(User, viewer.id)
    assert user.locked_until is not None
    assert not _logged_in(_login(TestClient(client.app), tenant.slug, viewer.email))


def test_mismatched_confirmation_is_rejected(client, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="pw-mismatch")
    _login(client, tenant.slug, users["viewer"].email)

    resp = _change(client, TenantFactory.PASSWORD, NEW_PASSWORD, confirm=NEW_PASSWORD + "x")

    assert resp.status_code == 400
    assert "don&#39;t match" in resp.text or "don't match" in resp.text


def test_new_password_must_differ_from_current(client, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="pw-same")
    _login(client, tenant.slug, users["viewer"].email)

    resp = _change(client, TenantFactory.PASSWORD, TenantFactory.PASSWORD)

    assert resp.status_code == 400
    assert "different" in resp.text


def test_new_password_must_meet_strictest_policy_across_organizations(client, db_session, tenant_factory):
    """One password serves every membership, so a looser organization can't be used to
    set a password a stricter one would reject."""
    tenant, _account, users = tenant_factory.make(slug="pw-strict-a")
    strict_tenant, _account2, _users2 = tenant_factory.make(slug="pw-strict-b")
    strict_tenant.password_min_length = 30
    strict_tenant.password_require_symbol = True
    viewer = users["viewer"]
    group = security_group_service.list_security_groups(db_session, strict_tenant.id)[0]
    user_service.confirm_cross_tenant_membership(db_session, strict_tenant.id, email=viewer.email, security_group_id=group.id)
    db_session.commit()
    _login(client, tenant.slug, viewer.email)

    resp = _change(client, TenantFactory.PASSWORD, "Only-twenty-one-chars1")

    assert resp.status_code == 400
    assert "at least 30 characters" in resp.text


def test_password_change_is_refused_in_demo_organization(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="pw-demo")
    tenant.is_demo = True
    db_session.commit()
    _login(client, tenant.slug, users["viewer"].email)

    resp = _change(client, TenantFactory.PASSWORD, NEW_PASSWORD)

    # Refused up front by the demo lock (web/demo_guard.py)...
    assert resp.status_code == 403
    assert "demo" in resp.text
    assert _logged_in(_login(TestClient(client.app), tenant.slug, users["viewer"].email))


def test_password_service_also_refuses_the_demo_organization(db_session, tenant_factory):
    """...and by the service itself, as a second layer if a route ever skips the lock."""
    import pytest

    tenant, _account, users = tenant_factory.make(slug="pw-demo-service")
    tenant.is_demo = True
    db_session.commit()

    with pytest.raises(user_service.PasswordChangeError, match="demo"):
        user_service.change_own_password(
            db_session, tenant.id, users["viewer"].id, current_password=TenantFactory.PASSWORD, new_password=NEW_PASSWORD
        )


# --- Admin reset ---


@pytest.mark.asvs("6.4.6")
def test_admin_reset_emails_a_one_time_link_the_admin_never_sees(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="pw-reset-flow")
    viewer = users["viewer"]
    _login(client, tenant.slug, users["admin"].email)

    resp = _reset(client, _membership(db_session, viewer, tenant).id)

    assert resp.status_code == 303 and "emailed%20a%20link" in resp.headers["location"]
    assert db_session.query(AuditLogEntry).filter_by(tenant_id=tenant.id, action="user.password_reset").count() == 1
    link = _reset_link(db_session, viewer)
    assert link.split("/")[-1] not in client.get("/ui/users").text  # nothing about the link on the admin's pages

    user_client = TestClient(client.app)
    assert not _logged_in(_login(user_client, tenant.slug, viewer.email))  # the old password no longer works
    page = user_client.get(link)
    assert page.status_code == 200 and viewer.email in page.text
    assert page.headers["cache-control"] == "no-store" and page.headers["referrer-policy"] == "no-referrer"

    weak = _choose(user_client, link, "Password1")
    assert weak.status_code == 422 and "at least" in weak.text
    done = _choose(user_client, link, NEW_PASSWORD)
    assert done.status_code == 303 and done.headers["location"].startswith(f"/ui/login/{tenant.slug}")
    assert _logged_in(_login(user_client, tenant.slug, viewer.email, NEW_PASSWORD))
    assert db_session.query(AuditLogEntry).filter_by(tenant_id=tenant.id, action="user.password_reset_completed").count() == 1

    again = TestClient(client.app).get(link)
    assert "can&#39;t be used" in again.text or "can't be used" in again.text  # works once


@pytest.mark.asvs("6.4.1")
def test_a_reset_link_expires_and_a_newer_one_replaces_it(client, db_session, tenant_factory):
    from datetime import datetime, timedelta, timezone

    from openclear.domain.password_reset import PasswordReset

    tenant, _account, users = tenant_factory.make(slug="pw-reset-expiry")
    viewer = users["viewer"]
    _login(client, tenant.slug, users["admin"].email)
    _reset(client, _membership(db_session, viewer, tenant).id)
    first = _reset_link(db_session, viewer)
    db_session.query(Notification).filter_by(recipient_user_id=viewer.id,
                                             notification_type=NotificationType.PASSWORD_RESET_LINK).delete()
    db_session.commit()
    _reset(client, _membership(db_session, viewer, tenant).id)
    second = _reset_link(db_session, viewer)

    assert "can" in TestClient(client.app).get(first).text.split("<h1>")[1][:30]  # replaced: "This link can't be used"
    row = db_session.query(PasswordReset).filter_by(user_id=viewer.id).one()
    row.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    db_session.commit()
    assert _choose(TestClient(client.app), second, NEW_PASSWORD).status_code == 410


def test_admin_reset_also_clears_a_lockout(client, db_session, tenant_factory):
    from datetime import datetime, timedelta, timezone

    tenant, _account, users = tenant_factory.make(slug="pw-reset-unlock")
    viewer = users["viewer"]
    viewer.locked_until = datetime.now(timezone.utc) + timedelta(minutes=10)
    viewer.failed_login_attempts = 5
    db_session.commit()
    _login(client, tenant.slug, users["admin"].email)

    _reset(client, _membership(db_session, viewer, tenant).id)
    user_client = TestClient(client.app)
    _choose(user_client, _reset_link(db_session, viewer), NEW_PASSWORD)

    assert _logged_in(_login(user_client, tenant.slug, viewer.email, NEW_PASSWORD))


def test_a_reset_needs_email_to_be_set_up(client, db_session, tenant_factory, monkeypatch):
    from openclear.config import get_settings

    tenant, _account, users = tenant_factory.make(slug="pw-reset-no-email")
    monkeypatch.setattr(get_settings(), "smtp_host", None)
    _login(client, tenant.slug, users["admin"].email)

    resp = _reset(client, _membership(db_session, users["viewer"], tenant).id)

    assert "Email%20isn" in resp.headers["location"] or "Email+isn" in resp.headers["location"]
    assert _logged_in(_login(TestClient(client.app), tenant.slug, users["viewer"].email))  # nothing changed


def test_admin_cannot_reset_user_who_belongs_to_another_organization(client, db_session, tenant_factory):
    """Decided 2026-09-22 (FIX_PLAN.md Phase 2): a User's password is global, so one
    bank's admin must not be able to take over that person's access at another bank."""
    tenant, _account, users = tenant_factory.make(slug="pw-reset-multi-a")
    other_tenant, _account2, _users2 = tenant_factory.make(slug="pw-reset-multi-b")
    viewer = users["viewer"]
    group = security_group_service.list_security_groups(db_session, other_tenant.id)[0]
    user_service.confirm_cross_tenant_membership(db_session, other_tenant.id, email=viewer.email, security_group_id=group.id)
    db_session.commit()
    _login(client, tenant.slug, users["admin"].email)

    resp = _reset(client, _membership(db_session, viewer, tenant).id)

    assert resp.status_code == 303
    assert "other%20organizations" in resp.headers["location"] or "other+organizations" in resp.headers["location"]
    assert _logged_in(_login(TestClient(client.app), tenant.slug, viewer.email))  # password unchanged


def test_admin_cannot_reset_own_password_from_users_page(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="pw-reset-self")
    admin = users["admin"]
    _login(client, tenant.slug, admin.email)

    resp = _reset(client, _membership(db_session, admin, tenant).id)

    assert resp.status_code == 303
    assert "error" in resp.headers["location"]
    assert 'reset-password' not in client.get("/ui/users").text.split(admin.email, 1)[1].split("</tr>", 1)[0]


def test_admin_cannot_reset_a_membership_from_another_tenant(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="pw-reset-foreign-a")
    other_tenant, _account2, other_users = tenant_factory.make(slug="pw-reset-foreign-b")
    _login(client, tenant.slug, users["admin"].email)

    resp = _reset(client, _membership(db_session, other_users["viewer"], other_tenant).id)

    assert resp.status_code == 303
    assert "not+found" in resp.headers["location"].lower() or "not%20found" in resp.headers["location"].lower()
    assert _logged_in(_login(TestClient(client.app), other_tenant.slug, other_users["viewer"].email))


def test_viewer_cannot_reset_passwords(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="pw-reset-viewer")
    _login(client, tenant.slug, users["viewer"].email)

    assert _reset(client, _membership(db_session, users["preparer"], tenant).id).status_code == 403


def test_customer_scoped_admin_cannot_reset_passwords(client, db_session, tenant_factory):
    """user:manage is masked out of every customer-scoped session."""
    tenant, _account, users = tenant_factory.make(slug="pw-reset-customer-scope")
    customer = customer_service.create_customer(db_session, tenant.id, customer_service.CustomerInput(customer_number="C1", name="C"))
    group = security_group_service.create_security_group(
        db_session, tenant.id, SecurityGroupInput(name="Cust Admin", permissions=["user:manage"])
    )
    user_service.create_user_with_membership(
        db_session, tenant.id, email="custadmin@example.com", password=TenantFactory.PASSWORD,
        security_group_id=group.id, customer_id=customer.id,
    )
    db_session.commit()
    _login(client, tenant.slug, "custadmin@example.com")

    assert _reset(client, _membership(db_session, users["viewer"], tenant).id).status_code == 403


