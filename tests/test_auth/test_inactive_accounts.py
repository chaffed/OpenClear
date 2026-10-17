# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""A bank's own lockout, and deactivating and removing inactive access
(services/inactivity_service.py; Settings → Lockout and inactive accounts)."""

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from openclear.auth.login_service import LOCKED_UNTIL_UNLOCKED, PasswordLoginOutcome, authenticate_password
from openclear.domain.audit_log_entry import AuditLogEntry
from openclear.domain.notification import Notification, NotificationType
from openclear.domain.security_event import SecurityEvent
from openclear.domain.tenant_membership import TenantMembership
from openclear.domain.user import User
from openclear.services import api_client_service, inactivity_service, security_group_service, user_service
from tests.conftest import TenantFactory, login_headers
from tests.test_web.test_web_bulk_ach import _login

NOW = datetime(2026, 9, 1, 12, tzinfo=timezone.utc)


def _membership(db_session, tenant, user) -> TenantMembership:
    return db_session.execute(select(TenantMembership).where(TenantMembership.tenant_id == tenant.id,
                                                             TenantMembership.user_id == user.id)).scalar_one()


def _idle(db_session, tenant, user, days: int) -> TenantMembership:
    membership = _membership(db_session, tenant, user)
    membership.last_active_at = NOW - timedelta(days=days)
    db_session.commit()
    return membership


def _rules(db_session, tenant, *, deactivate=None, remove=None):
    tenant.deactivate_inactive, tenant.deactivate_inactive_days = deactivate is not None, deactivate or 90
    tenant.remove_inactive, tenant.remove_inactive_days = remove is not None, remove or 365
    db_session.commit()


def test_a_banks_own_lockout(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="lock-own")
    tenant.login_max_failed_attempts, tenant.login_lockout_minutes = 3, 5
    db_session.commit()
    email = users["viewer"].email
    for _ in range(2):
        assert authenticate_password(db_session, tenant.slug, email, "wrong").outcome == PasswordLoginOutcome.INVALID_CREDENTIALS
    assert authenticate_password(db_session, tenant.slug, email, "wrong").outcome == PasswordLoginOutcome.LOCKED  # the third
    db_session.commit()
    locked = db_session.get(User, users["viewer"].id).locked_until
    assert timedelta(minutes=4) < locked.replace(tzinfo=timezone.utc) - datetime.now(timezone.utc) <= timedelta(minutes=5)

    tenant.lockout_until_unlocked = True
    db_session.commit()
    other = users["preparer"].email
    for _ in range(3):
        authenticate_password(db_session, tenant.slug, other, "wrong")
    db_session.commit()
    assert db_session.get(User, users["preparer"].id).locked_until.replace(tzinfo=timezone.utc) == LOCKED_UNTIL_UNLOCKED
    note = db_session.execute(select(Notification).where(Notification.recipient_user_id == users["preparer"].id,
                                                         Notification.notification_type == NotificationType.ACCOUNT_LOCKED)).scalars().first()
    assert "until an administrator unlocks it" in note.body
    client.get("/ui/login")
    page = client.post("/ui/login", data={"tenant_slug": tenant.slug, "email": other, "password": TenantFactory.PASSWORD,
                                          "csrf_token": client.cookies.get("csrf_token"), "next": "/ui/"})
    assert "Ask an administrator to unlock your account." in page.text


def test_a_sign_in_counts_for_that_bank_only(client, db_session, tenant_factory):
    first, _a, users = tenant_factory.make(slug="active-one")
    second, _b, _users = tenant_factory.make(slug="active-two")
    viewer = next(g for g in security_group_service.list_security_groups(db_session, second.id) if g.name == "Viewer")
    db_session.add(TenantMembership(user_id=users["viewer"].id, tenant_id=second.id, security_group_id=viewer.id))
    db_session.commit()
    login_headers(client, first.slug, users["viewer"].email)
    db_session.expire_all()
    assert _membership(db_session, first, users["viewer"]).last_active_at is not None
    assert _membership(db_session, second, users["viewer"]).last_active_at is None


def test_off_by_default(db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="idle-off")
    _idle(db_session, tenant, users["viewer"], 1000)
    assert inactivity_service.sweep(db_session, tenant, NOW) == inactivity_service.Swept()


def test_warned_then_deactivated_then_removed(db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="idle-rules")
    _rules(db_session, tenant, deactivate=30, remove=90)
    admin_group = next(g for g in security_group_service.list_security_groups(db_session, tenant.id) if g.name == "Admin")
    robot = api_client_service.create_client(db_session, tenant.id, name="Robot", security_group_id=admin_group.id)
    db_session.commit()
    robot_membership = _membership(db_session, tenant, db_session.get(User, robot.client.user_id))
    robot_membership.last_active_at = NOW - timedelta(days=500)
    _idle(db_session, tenant, users["admin"], 1)
    _idle(db_session, tenant, users["approver"], 25)  # deactivated in 5 days: warned
    _idle(db_session, tenant, users["preparer"], 40)  # past 30: deactivated
    _idle(db_session, tenant, users["viewer"], 100)  # past 90: removed

    done = inactivity_service.sweep(db_session, tenant, NOW)
    db_session.commit()
    assert (done.warned, done.deactivated, done.removed) == ([users["approver"].email], [users["preparer"].email], [users["viewer"].email])
    assert _membership(db_session, tenant, users["preparer"]).is_active is False
    assert db_session.execute(select(TenantMembership).where(TenantMembership.user_id == users["viewer"].id)).first() is None
    assert db_session.get(User, users["viewer"].id) is not None  # the person stays, for the records
    assert db_session.get(TenantMembership, robot_membership.id).is_active  # API clients are never touched
    warning = db_session.execute(select(Notification).where(Notification.notification_type == NotificationType.INACTIVITY_WARNING)).scalar_one()
    assert "turned off in 5 days" in warning.body
    actions = dict(db_session.execute(select(AuditLogEntry.action, AuditLogEntry.summary).where(AuditLogEntry.tenant_id == tenant.id,
                   AuditLogEntry.action.in_(("user.deactivate", "user.remove_inactive")))).all())
    assert actions["user.remove_inactive"].endswith("no sign-in for 90 days")
    events = db_session.execute(select(SecurityEvent.name).where(SecurityEvent.tenant_id == tenant.id,
                                                                 SecurityEvent.name == "user.deactivated")).scalars().all()
    assert len(events) == 2

    assert inactivity_service.sweep(db_session, tenant, NOW + timedelta(days=1)).warned == []  # warned once
    user_service.reactivate_membership(db_session, tenant.id, _membership(db_session, tenant, users["preparer"]).id)
    db_session.commit()
    again = inactivity_service.sweep(db_session, tenant, NOW + timedelta(days=1))
    assert users["preparer"].email not in again.deactivated  # reactivated: a fresh start


def test_the_last_administrator_is_kept(db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="idle-admin")
    _rules(db_session, tenant, deactivate=30)
    for role, days in (("admin", 60), ("approver", 60), ("preparer", 60), ("viewer", 60), ("bookkeeper", 50)):
        _idle(db_session, tenant, users[role], days)
    managers = {name for name, user in users.items()
                if "user:manage" in next(g for g in security_group_service.list_security_groups(db_session, tenant.id)
                                         if g.id == _membership(db_session, tenant, user).security_group_id).permissions}
    done = inactivity_service.sweep(db_session, tenant, NOW)
    kept = done.kept_last_admin
    assert kept in {users[m].email for m in managers} and kept not in done.deactivated
    assert len(done.deactivated) == len(users) - 1


def test_the_settings_page(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="idle-page")
    csrf = _login(client, tenant.slug, users["admin"].email)
    form = {"csrf_token": csrf, "max_failed_attempts": "4", "lockout_minutes": "", "deactivate_inactive": "true",
            "deactivate_inactive_days": "60", "remove_inactive": "true", "remove_inactive_days": "45"}
    bad = client.post("/ui/settings/access-rules", data=form)
    assert "Remove after more days than you deactivate after." in bad.text
    saved = client.post("/ui/settings/access-rules", data={**form, "remove_inactive_days": "180"}, follow_redirects=False)
    assert saved.status_code in (200, 303)
    db_session.expire_all()
    fresh = db_session.get(type(tenant), tenant.id)
    assert (fresh.login_max_failed_attempts, fresh.login_lockout_minutes, fresh.deactivate_inactive_days, fresh.remove_inactive_days) == (4, None, 60, 180)
    summary = db_session.execute(select(AuditLogEntry.summary).where(AuditLogEntry.action == "tenant.update_access_rules")).scalar_one()
    assert "lock after 4 failed attempts for 15 minutes" in summary and "remove after 180 days inactive" in summary
    assert "Inactive: deactivate after 60 days, remove after 180 days" in client.get("/ui/settings").text
