# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""A second person for settings changes (DUAL_CONTROL_PLAN.md DC0, DC1;
services/change_request_service.py, change_kinds.py)."""

from datetime import timedelta

import pytest
from sqlalchemy import select

from openclear.auth import sod
from openclear.domain.audit_log_entry import AuditLogEntry
from openclear.domain.change_request import ChangeRequest
from openclear.domain.notification import Notification, NotificationType
from openclear.domain.security_event import SecurityEvent
from openclear.domain.tenant import Tenant
from openclear.domain.tenant_membership import TenantMembership
from openclear.services import change_request_service as changes
from openclear.services import security_group_service, user_service
from openclear.services.change_kinds import ChangeError
from tests.conftest import TenantFactory, login_headers


def _second_admin(db_session, tenant, name="second"):
    group = security_group_service.get_security_group_by_name(db_session, tenant.id, "Admin")
    user = user_service.create_user_with_membership(db_session, tenant.id, email=f"{name}@{tenant.slug}.example.com",
                                                    password=TenantFactory.PASSWORD, security_group_id=group.id)
    db_session.commit()
    return user


def _only(db_session, tenant, keep: str, users):
    """Deactivate everyone but `keep`, so nobody else can approve."""
    for name, user in users.items():
        if name != keep:
            for m in db_session.execute(select(TenantMembership).where(TenantMembership.user_id == user.id)).scalars():
                m.is_active = False
    db_session.commit()


def _policy(**over) -> dict:
    values = {"password_min_length": 16, "password_require_uppercase": True, "password_require_lowercase": True,
              "password_require_number": True, "password_require_symbol": False, **over}
    return {"values": values}


def test_tightening_applies_at_once_and_loosening_waits(db_session, tenant_factory):
    tenant, _a, users = tenant_factory.make(slug="dc-basic")
    second = _second_admin(db_session, tenant)
    admin = users["admin"]
    stronger = changes.request_or_apply(db_session, "password_policy", _policy(), tenant_id=tenant.id, user_id=admin.id)
    db_session.commit()
    assert stronger.applied and db_session.get(Tenant, tenant.id).password_min_length == 16

    weaker = changes.request_or_apply(db_session, "password_policy", _policy(password_min_length=8), tenant_id=tenant.id, user_id=admin.id)
    db_session.commit()
    assert weaker.waiting and weaker.change.applies_at is None and db_session.get(Tenant, tenant.id).password_min_length == 16
    assert weaker.change.before["password_min_length"] == 16 and weaker.change.after["password_min_length"] == 8
    told = db_session.execute(select(Notification.recipient_user_id).where(Notification.notification_type == NotificationType.CHANGE_REQUESTED)).scalars().all()
    assert second.id in told and admin.id not in told

    with pytest.raises(ChangeError, match="already waiting"):
        changes.request_or_apply(db_session, "password_policy", _policy(password_min_length=6), tenant_id=tenant.id, user_id=admin.id)
    with pytest.raises(sod.SodViolation):
        changes.decide(db_session, tenant.id, weaker.change.id, approve=True, user_id=admin.id)
    with pytest.raises(ChangeError, match="could have made it"):
        changes.decide(db_session, tenant.id, weaker.change.id, approve=True, user_id=users["viewer"].id)
    with pytest.raises(ChangeError, match="Say why"):
        changes.decide(db_session, tenant.id, weaker.change.id, approve=False, user_id=second.id)
    changes.decide(db_session, tenant.id, weaker.change.id, approve=True, user_id=second.id)
    db_session.commit()
    assert db_session.get(Tenant, tenant.id).password_min_length == 8
    actions = db_session.execute(select(AuditLogEntry.action).where(AuditLogEntry.tenant_id == tenant.id)).scalars().all()
    assert {"change_request.create", "change_request.approve", "tenant.update_password_policy"} <= set(actions)
    assert db_session.execute(select(Notification).where(Notification.notification_type == NotificationType.CHANGE_DECIDED,
                                                         Notification.recipient_user_id == admin.id)).first()


def test_turning_enforce_back_to_warn_needs_a_second_admin(db_session, tenant_factory):
    from tests.test_auth.test_sod import _separate_duties

    tenant, _a, users = tenant_factory.make(slug="dc-sod")
    second = _second_admin(db_session, tenant)
    _separate_duties(db_session, tenant)
    assert changes.request_or_apply(db_session, "sod_mode", {"values": {"sod_mode": "enforce"}}, tenant_id=tenant.id,
                                    user_id=users["admin"].id).applied  # tightening
    db_session.commit()
    back = changes.request_or_apply(db_session, "sod_mode", {"values": {"sod_mode": "warn"}}, tenant_id=tenant.id, user_id=users["admin"].id)
    db_session.commit()
    assert back.waiting and sod.mode(db_session, tenant.id) == "enforce"
    changes.decide(db_session, tenant.id, back.change.id, approve=True, user_id=second.id)
    db_session.commit()
    assert sod.mode(db_session, tenant.id) == "warn"


def test_approval_refused_when_the_setting_changed_meanwhile(db_session, tenant_factory):
    tenant, _a, users = tenant_factory.make(slug="dc-stale")
    second = _second_admin(db_session, tenant)
    changes.request_or_apply(db_session, "password_policy", _policy(), tenant_id=tenant.id, user_id=users["admin"].id)
    waiting = changes.request_or_apply(db_session, "password_policy", _policy(password_min_length=8), tenant_id=tenant.id,
                                       user_id=users["admin"].id)
    db_session.get(Tenant, tenant.id).password_min_length = 14  # changed some other way meanwhile
    db_session.commit()
    with pytest.raises(ChangeError, match="changed since"):
        changes.decide(db_session, tenant.id, waiting.change.id, approve=True, user_id=second.id)


def test_a_switch_turns_on_only_with_someone_else_to_approve(client, db_session, tenant_factory):
    tenant, _a, users = tenant_factory.make(slug="dc-switch")
    _only(db_session, tenant, "admin", users)
    with pytest.raises(ChangeError, match="Nobody else can approve"):
        changes.request_or_apply(db_session, "approval_switches", {"values": {"sensitive_changes_need_approval": True}},
                                 tenant_id=tenant.id, user_id=users["admin"].id)
    db_session.expire_all()
    assert db_session.get(Tenant, tenant.id).sensitive_changes_need_approval is False
    refused = db_session.execute(select(AuditLogEntry).where(AuditLogEntry.tenant_id == tenant.id,
                                                             AuditLogEntry.action == "change_request.switch_refused")).scalar_one()
    assert "admin:manage" in refused.summary
    with pytest.raises(ChangeError, match="pay and return decisions"):
        changes.request_or_apply(db_session, "dual_control", {"values": {"require_dual_control": True}}, tenant_id=tenant.id,
                                 user_id=users["admin"].id)

    # On the page: the switch stays off and says who's missing.
    from tests.test_web.test_web_bulk_ach import _login

    csrf = _login(client, tenant.slug, users["admin"].email)
    page = client.post("/ui/settings/dual-control", data={"csrf_token": csrf, "access_changes_need_approval": "true"})
    assert page.status_code == 422 and "Nobody else can approve people&#39;s access" in page.text

    _second_admin(db_session, tenant)
    assert changes.request_or_apply(db_session, "approval_switches", {"values": {"sensitive_changes_need_approval": True}},
                                    tenant_id=tenant.id, user_id=users["admin"].id).applied
    db_session.commit()
    assert db_session.get(Tenant, tenant.id).sensitive_changes_need_approval is True


def test_with_nobody_else_it_applies_after_a_cooling_off(db_session, tenant_factory):
    tenant, _a, users = tenant_factory.make(slug="dc-alone")
    changes.request_or_apply(db_session, "password_policy", _policy(), tenant_id=tenant.id, user_id=users["admin"].id)
    _only(db_session, tenant, "admin", users)
    waiting = changes.request_or_apply(db_session, "password_policy", _policy(password_min_length=8), tenant_id=tenant.id,
                                       user_id=users["admin"].id)
    db_session.commit()
    assert waiting.change.applies_at is not None
    assert changes.settle(db_session, waiting.change.requested_at + timedelta(hours=23)) == 0
    assert changes.settle(db_session, waiting.change.requested_at + timedelta(hours=25)) == 1
    db_session.expire_all()
    assert db_session.get(Tenant, tenant.id).password_min_length == 8
    assert db_session.get(ChangeRequest, waiting.change.id).status == "applied_alone"
    critical = db_session.execute(select(SecurityEvent).where(SecurityEvent.tenant_id == tenant.id,
                                                              SecurityEvent.name == "change_request.applied_alone")).scalar_one()
    assert critical.severity == "critical"


def test_expiry_and_withdrawal(db_session, tenant_factory):
    tenant, _a, users = tenant_factory.make(slug="dc-expire")
    second = _second_admin(db_session, tenant)
    changes.request_or_apply(db_session, "password_policy", _policy(), tenant_id=tenant.id, user_id=users["admin"].id)
    first = changes.request_or_apply(db_session, "password_policy", _policy(password_min_length=8), tenant_id=tenant.id,
                                     user_id=users["admin"].id)
    db_session.commit()
    with pytest.raises(ChangeError, match="Only whoever asked"):
        changes.withdraw(db_session, tenant.id, first.change.id, user_id=second.id)
    changes.withdraw(db_session, tenant.id, first.change.id, user_id=users["admin"].id)
    db_session.commit()
    again = changes.request_or_apply(db_session, "password_policy", _policy(password_min_length=8), tenant_id=tenant.id,
                                     user_id=users["admin"].id)
    db_session.commit()
    assert changes.settle(db_session, again.change.requested_at + timedelta(days=8)) == 1
    db_session.expire_all()
    assert db_session.get(ChangeRequest, again.change.id).status == "expired"
    assert db_session.get(Tenant, tenant.id).password_min_length == 16


def test_the_changes_page_and_api(client, db_session, tenant_factory):
    tenant, _a, users = tenant_factory.make(slug="dc-api")
    second = _second_admin(db_session, tenant)
    changes.request_or_apply(db_session, "password_policy", _policy(), tenant_id=tenant.id, user_id=users["admin"].id)
    waiting = changes.request_or_apply(db_session, "password_policy", _policy(password_min_length=8), tenant_id=tenant.id,
                                       user_id=users["admin"].id, reason="Customers keep forgetting")
    db_session.commit()

    mine = login_headers(client, tenant.slug, users["admin"].email)
    listed = client.get("/api/v1/changes", headers=mine).json()
    assert [(c["kind"], c["can_decide"], c["reason"]) for c in listed] == [("password_policy", False, "Customers keep forgetting")]
    own = client.post(f"/api/v1/changes/{waiting.change.id}/approve", headers=mine)
    assert own.status_code == 403 and own.json()["detail"]["code"] == "ERR_SOD_VIOLATION"
    theirs = login_headers(client, tenant.slug, second.email)
    assert client.post(f"/api/v1/changes/{waiting.change.id}/reject", headers=theirs, json={"reason": ""}).status_code == 422
    approved = client.post(f"/api/v1/changes/{waiting.change.id}/approve", headers=theirs)
    assert approved.status_code == 200 and approved.json()["status"] == "approved"

    from tests.test_web.test_web_bulk_ach import _login

    _login(client, tenant.slug, second.email)
    page = client.get("/ui/admin/changes")
    assert page.status_code == 200 and "Recently decided" in page.text and "approved" in page.text


def test_the_evidence_export_lists_the_periods_changes(db_session, tenant_factory):
    from datetime import datetime, timezone

    from openclear.services import evidence_service

    tenant, _a, users = tenant_factory.make(slug="dc-evidence")
    second = _second_admin(db_session, tenant)
    changes.request_or_apply(db_session, "password_policy", _policy(), tenant_id=tenant.id, user_id=users["admin"].id)
    waiting = changes.request_or_apply(db_session, "password_policy", _policy(password_min_length=8), tenant_id=tenant.id,
                                       user_id=users["admin"].id)
    changes.decide(db_session, tenant.id, waiting.change.id, approve=True, user_id=second.id)
    db_session.commit()
    today = datetime.now(timezone.utc).date()  # changes are stamped in UTC; local today differs near midnight
    listed = evidence_service.build(db_session, tenant.id, start=today, end=today, generated_by_user_id=None)["change_control"]
    assert [(c["kind"], c["status"], c["requested_by"], c["decided_by"]) for c in listed] == \
        [("password_policy", "approved", users["admin"].email, second.email)]
    assert listed[0]["before"]["password_min_length"] == 16 and listed[0]["after"]["password_min_length"] == 8


def test_admins_are_told_when_a_rule_loses_its_last_other_approver(db_session, tenant_factory):
    from openclear.services import controls_service

    tenant, _a, users = tenant_factory.make(slug="dc-gone")
    second = _second_admin(db_session, tenant)
    controls_service.status(db_session, tenant.id)  # the first status: everyone can be approved
    for m in db_session.execute(select(TenantMembership).where(TenantMembership.user_id == second.id)).scalars():
        m.is_active = False  # the other admin leaves; that never waits (DC-D17)
    db_session.commit()
    controls_service.status(db_session, tenant.id)
    told = db_session.execute(select(Notification.recipient_user_id).where(Notification.notification_type == NotificationType.APPROVER_GONE)).scalars().all()
    assert told and users["admin"].id in told and second.id not in told
    controls_service.status(db_session, tenant.id)  # once, not on every look
    again = db_session.execute(select(Notification.recipient_user_id).where(Notification.notification_type == NotificationType.APPROVER_GONE)).scalars().all()
    assert len(again) == len(told)
