# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Segregation of duties (COMPLIANCE_PLAN.md C1; auth/sod.py, services/change_request_service.py):
conflicting duties in groups, the presenter of an item deciding it, two-person changes to
rules and models, and the audit trail of every breach attempt, refused or not."""

import uuid
from datetime import date

from sqlalchemy import select

from openclear.auth import sod
from openclear.auth.permissions import DEFAULT_SECURITY_GROUPS
from openclear.domain.audit_log_entry import AuditLogEntry
from openclear.domain.ml_model import MlModelStatus
from openclear.domain.tenant_membership import TenantMembership
from openclear.ml.registry import create_model_row
from openclear.repositories.exception_repo import ExceptionRepository
from openclear.services import security_group_service, user_service
from tests.conftest import TenantFactory, login_headers
from tests.test_auth.test_plugins_bank_choices import example_plugin  # noqa: F401 -- fixture
from tests.test_web.test_web_customer_ml import _make_customer, _seed_labeled_decisions


def _violations(db_session, tenant):
    return db_session.execute(select(AuditLogEntry).where(AuditLogEntry.tenant_id == tenant.id, AuditLogEntry.action == "sod.violation")
                              ).scalars().all()


def _separate_duties(db_session, tenant):
    """Fix the seeded groups that combine duties, so enforcement can be turned on."""
    for name in ("Admin", "Bookkeeper"):
        group = security_group_service.get_security_group_by_name(db_session, tenant.id, name)
        keep = [p for p in group.permissions if p not in sod.INGEST | {"security_group:manage"}]
        security_group_service.update_security_group(db_session, tenant.id, group.id,
                                                     security_group_service.SecurityGroupInput(name=name, permissions=keep))
    db_session.commit()


def test_which_default_groups_combine_duties():
    conflicted = {name for name, perms in DEFAULT_SECURITY_GROUPS.items() if sod.group_conflicts(perms)}
    assert conflicted == {"Admin", "Bookkeeper"}


def _present_and_regroup(client, db_session, tenant, account, users):
    """The preparer presents a check through the API; later they're moved to Approver."""
    headers = login_headers(client, tenant.slug, users["preparer"].email)
    presented = client.post("/api/v1/paid-items", headers=headers, json={
        "account_id": str(account.id), "check_number": "SOD-1", "presented_amount": "50.00", "presented_date": str(date(2026, 3, 2))})
    assert presented.status_code == 201, presented.text
    approver = security_group_service.get_security_group_by_name(db_session, tenant.id, "Approver")
    membership = db_session.execute(select(TenantMembership).where(TenantMembership.user_id == users["preparer"].id,
                                                                   TenantMembership.tenant_id == tenant.id)).scalar_one()
    membership.security_group_id = approver.id
    db_session.commit()
    return ExceptionRepository(db_session, tenant.id).list(source_item_id=uuid.UUID(presented.json()["id"]))[0]


def test_warn_mode_lets_the_presenter_decide_but_logs_it(client, db_session, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="sod-warn")
    exception = _present_and_regroup(client, db_session, tenant, account, users)
    decided = client.post(f"/api/v1/exceptions/{exception.id}/decide", headers=login_headers(client, tenant.slug, users["preparer"].email),
                          json={"outcome": "pay", "reason_code": "ok"})
    assert decided.status_code in (200, 201), decided.text
    [entry] = _violations(db_session, tenant)
    assert entry.metadata_json["rule"] == "presenter_decides" and entry.metadata_json["enforced"] is False


def test_enforce_mode_refuses_the_presenter_and_keeps_the_record(client, db_session, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="sod-enforce")
    try:
        sod.set_mode(db_session, tenant.id, "enforce")
        raise AssertionError("enforcement must be refused while groups combine duties")
    except ValueError as exc:
        assert "Admin" in str(exc) and "Bookkeeper" in str(exc)
    db_session.rollback()
    _separate_duties(db_session, tenant)
    sod.set_mode(db_session, tenant.id, "enforce")
    db_session.commit()

    exception = _present_and_regroup(client, db_session, tenant, account, users)
    refused = client.post(f"/api/v1/exceptions/{exception.id}/decide", headers=login_headers(client, tenant.slug, users["preparer"].email),
                          json={"outcome": "pay", "reason_code": "ok"})
    assert refused.status_code == 403 and refused.json()["detail"]["code"] == "ERR_SOD_VIOLATION"
    db_session.expire_all()
    assert ExceptionRepository(db_session, tenant.id).get(exception.id).status.value == "open"  # nothing was decided
    [entry] = _violations(db_session, tenant)
    assert entry.metadata_json["enforced"] is True and entry.metadata_json["severity"] == "critical"

    # Someone else may decide it.
    other = client.post(f"/api/v1/exceptions/{exception.id}/decide", headers=login_headers(client, tenant.slug, users["approver"].email),
                        json={"outcome": "pay", "reason_code": "ok"})
    assert other.status_code in (200, 201)


def test_a_group_combining_duties_cant_be_saved_under_enforcement(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="sod-groups")
    _separate_duties(db_session, tenant)
    # The admin needs security_group:manage back to edit groups, but not user:manage alongside it.
    admin_group = security_group_service.get_security_group_by_name(db_session, tenant.id, "Admin")
    perms = [p for p in admin_group.permissions if p != "user:manage"] + ["security_group:manage"]
    security_group_service.update_security_group(db_session, tenant.id, admin_group.id,
                                                 security_group_service.SecurityGroupInput(name="Admin", permissions=perms))
    sod.set_mode(db_session, tenant.id, "enforce")
    db_session.commit()
    client.get("/ui/login")
    csrf = client.cookies.get("csrf_token")
    client.post("/ui/login", data={"tenant_slug": tenant.slug, "email": users["admin"].email, "password": TenantFactory.PASSWORD,
                                   "csrf_token": csrf, "next": "/ui/"})
    refused = client.post("/ui/security-groups", data={"csrf_token": csrf, "name": "Clerk",
                                                        "permissions": ["paid_item:write", "exception:decide"]})
    assert refused.status_code == 403 and "submits items" in refused.text
    assert security_group_service.get_security_group_by_name(db_session, tenant.id, "Clerk") is None
    allowed = client.post("/ui/security-groups", data={"csrf_token": csrf, "name": "Clerk", "permissions": ["paid_item:write"]},
                          follow_redirects=False)
    assert allowed.status_code == 303


def test_model_changes_need_a_second_admin_under_enforcement(client, db_session, tenant_factory):
    from openclear.ml.train import train_model

    tenant, _account, users = tenant_factory.make(slug="sod-changes")
    customer, customer_account = _make_customer(db_session, tenant)
    _seed_labeled_decisions(db_session, tenant, customer_account, users)
    first = train_model(db_session, "check", customer_id=customer.id)
    second = create_model_row(db_session, network_code="check", customer_id=customer.id, version="v-sod", algorithm="logistic_regression",
                              artifact_path=first.model_row.artifact_path, trained_from_decision_count=12, metrics_json={},
                              status=MlModelStatus.RETIRED)
    _separate_duties(db_session, tenant)
    sod.set_mode(db_session, tenant.id, "enforce")
    admin_group = security_group_service.get_security_group_by_name(db_session, tenant.id, "Admin")
    second_admin = user_service.create_user_with_membership(db_session, tenant.id, email="second.admin@sod-changes.example.com",
                                                            password=TenantFactory.PASSWORD, security_group_id=admin_group.id)
    db_session.commit()

    def login(email):
        client.cookies.clear()
        client.get("/ui/login")
        csrf = client.cookies.get("csrf_token")
        client.post("/ui/login", data={"tenant_slug": tenant.slug, "email": email, "password": TenantFactory.PASSWORD,
                                       "csrf_token": csrf, "next": "/ui/"})
        return csrf

    csrf = login(users["admin"].email)
    client.post(f"/ui/admin/customers/{customer.id}/ml/models/{second.id}/activate", data={"csrf_token": csrf})
    db_session.expire_all()
    assert db_session.get(type(second), second.id).status == MlModelStatus.RETIRED  # not yet
    page = client.get("/ui/admin/changes")
    change_id = page.text.split("/ui/admin/changes/")[1].split("/")[0]
    own = client.post(f"/ui/admin/changes/{change_id}/approve", data={"csrf_token": csrf})
    assert own.status_code == 403  # the requester can't approve their own change

    csrf = login(second_admin.email)
    client.post(f"/ui/admin/changes/{change_id}/approve", data={"csrf_token": csrf})
    db_session.expire_all()
    assert db_session.get(type(second), second.id).status == MlModelStatus.ACTIVE
    actions = [e.action for e in db_session.execute(select(AuditLogEntry).where(AuditLogEntry.tenant_id == tenant.id)).scalars()]
    assert {"change_request.create", "change_request.approve", "sod.violation"} <= set(actions)


def test_a_matching_rule_waits_for_a_second_admin(example_plugin, client, db_session, tenant_factory):  # noqa: F811
    from openclear.domain.change_request import ChangeRequest
    from openclear.services import change_request_service, plugin_service

    tenant, _account, users = tenant_factory.make(slug="sod-rules")
    _separate_duties(db_session, tenant)
    sod.set_mode(db_session, tenant.id, "enforce")
    db_session.commit()
    client.get("/ui/login")
    csrf = client.cookies.get("csrf_token")
    client.post("/ui/login", data={"tenant_slug": tenant.slug, "email": users["admin"].email, "password": TenantFactory.PASSWORD,
                                   "csrf_token": csrf, "next": "/ui/"})
    asked = client.post("/ui/admin/plugins/choices", data={"csrf_token": csrf, "point": "matching_rules", "name": "example_large_round_amount",
                                                   "enabled": "true"}, follow_redirects=False)
    assert asked.status_code == 303 and "second+admin" in asked.headers["location"]
    assert not plugin_service.is_enabled(db_session, tenant.id, "matching_rules", "example_large_round_amount")
    [change] = db_session.execute(select(ChangeRequest).where(ChangeRequest.tenant_id == tenant.id)).scalars().all()
    change_request_service.decide(db_session, tenant.id, change.id, approve=True, user_id=users["approver"].id)
    db_session.commit()
    assert plugin_service.is_enabled(db_session, tenant.id, "matching_rules", "example_large_round_amount")


def test_the_api_cant_skip_the_second_admin(client, db_session, tenant_factory):
    from openclear.domain.change_request import ChangeRequest
    from openclear.domain.ml_model import MlModelStatus
    from tests.test_api.test_admin_ml import _bank_only

    tenant, _account, users = tenant_factory.make(slug="sod-api-model")
    _bank_only(db_session, tenant)
    row = create_model_row(db_session, network_code="check", version="v-api", algorithm="logistic_regression", artifact_path="/tmp/x.joblib",
                           trained_from_decision_count=10, metrics_json={}, status=MlModelStatus.RETIRED, tenant_id=tenant.id)
    _separate_duties(db_session, tenant)
    sod.set_mode(db_session, tenant.id, "enforce")
    db_session.commit()
    headers = login_headers(client, tenant.slug, users["admin"].email)

    pending = client.patch(f"/api/v1/admin/ml/models/{row.id}/activate", headers=headers)
    assert pending.status_code == 202 and pending.json()["status"] == "pending"
    db_session.expire_all()
    assert db_session.get(type(row), row.id).status == MlModelStatus.RETIRED
    change = db_session.get(ChangeRequest, uuid.UUID(pending.json()["change_request_id"]))
    assert change.summary == "Activate check model v-api (bank-only)"
    # A model that isn't this bank's can't even be asked for.
    assert client.patch(f"/api/v1/admin/ml/models/{uuid.uuid4()}/activate", headers=headers).status_code == 404
