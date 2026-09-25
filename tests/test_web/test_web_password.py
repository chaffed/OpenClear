# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Password lifecycle: self-service change (/ui/security/password) and admin reset with a
forced change at next sign-in (/ui/users/{membership_id}/reset-password). See
services/user_service.py::change_own_password / admin_reset_password."""

import re

from fastapi.testclient import TestClient

from openclear.auth.password_policy import PasswordPolicy, validate_password
from openclear.domain.audit_log_entry import AuditLogEntry
from openclear.domain.notification import Notification, NotificationChannel, NotificationType
from openclear.domain.tenant_membership import TenantMembership
from openclear.domain.user import User
from openclear.services import customer_service, security_group_service, user_service
from openclear.services.security_group_service import SecurityGroupInput
from tests.conftest import TenantFactory

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


def _temporary_password(resp) -> str:
    return re.search(r'id="temporary-password"[^>]*>([^<]+)<', resp.text).group(1)


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


def test_admin_reset_issues_temporary_password_and_forces_change(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="pw-reset-flow")
    viewer = users["viewer"]
    _login(client, tenant.slug, users["admin"].email)

    resp = _reset(client, _membership(db_session, viewer, tenant).id)

    assert resp.status_code == 200
    assert resp.headers["cache-control"] == "no-store"
    temporary = _temporary_password(resp)
    assert db_session.query(AuditLogEntry).filter_by(tenant_id=tenant.id, action="user.password_reset").count() == 1
    assert temporary not in "".join(e.summary for e in db_session.query(AuditLogEntry).all())

    user_client = TestClient(client.app)
    assert not _logged_in(_login(user_client, tenant.slug, viewer.email))  # old password no longer works
    assert _logged_in(_login(user_client, tenant.slug, viewer.email, temporary))

    # Every page except the change form redirects there until the password is changed.
    blocked = user_client.get("/ui/exceptions", follow_redirects=False)
    assert blocked.status_code == 303
    assert blocked.headers["location"].startswith("/ui/security/password")
    form = user_client.get("/ui/security/password")
    assert form.status_code == 200
    assert "An administrator reset your password" in form.text
    assert 'href="/ui/exceptions"' not in form.text  # nav links hidden: they'd only bounce back here
    assert 'action="/ui/logout"' in form.text

    done = _change(user_client, temporary, NEW_PASSWORD)
    assert done.status_code == 303
    assert done.headers["location"].startswith("/ui/?")
    assert user_client.get("/ui/exceptions", follow_redirects=False).status_code == 200


def test_temporary_password_cannot_be_used_on_the_api(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="pw-reset-api")
    viewer = users["viewer"]
    _login(client, tenant.slug, users["admin"].email)
    temporary = _temporary_password(_reset(client, _membership(db_session, viewer, tenant).id))

    login = client.post("/api/v1/auth/login", json={"tenant_slug": tenant.slug, "email": viewer.email, "password": temporary})
    token = login.json()["access_token"]
    resp = client.get("/api/v1/exceptions", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 403
    assert "Password change required" in resp.json()["detail"]


def test_admin_reset_also_clears_a_lockout(client, db_session, tenant_factory):
    from datetime import datetime, timedelta, timezone

    tenant, _account, users = tenant_factory.make(slug="pw-reset-unlock")
    viewer = users["viewer"]
    viewer.locked_until = datetime.now(timezone.utc) + timedelta(minutes=10)
    viewer.failed_login_attempts = 5
    db_session.commit()
    _login(client, tenant.slug, users["admin"].email)

    temporary = _temporary_password(_reset(client, _membership(db_session, viewer, tenant).id))

    assert _logged_in(_login(TestClient(client.app), tenant.slug, viewer.email, temporary))


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


def test_generated_temporary_password_satisfies_a_strict_policy():
    from openclear.services.user_service import _generate_temporary_password

    policy = PasswordPolicy(min_length=24, require_uppercase=True, require_lowercase=True, require_number=True, require_symbol=True)
    passwords = {_generate_temporary_password(policy) for _ in range(50)}

    assert len(passwords) == 50
    for password in passwords:
        validate_password(password, policy)
        assert len(password) >= 24
