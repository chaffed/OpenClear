# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed
"""Required security keys (OWASP_PLAN.md OW-D18–D20; auth/security_key_policy.py): a bank or a
customer requires one for password sign-ins, after a grace period; turning it on applies at
once and turning it off waits for a second admin."""

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from openclear.auth.login_service import PasswordLoginOutcome, authenticate_password
from openclear.auth.security_key_policy import requirement
from openclear.domain.tenant_membership import TenantMembership
from openclear.domain.webauthn_credential import WebauthnCredential
from openclear.services import change_request_service, customer_service
from openclear.services.tenant_service import set_security_key_requirement
from tests.conftest import TenantFactory


def _membership(db_session, tenant, user):
    return db_session.query(TenantMembership).filter_by(tenant_id=tenant.id, user_id=user.id).first()


def _require(db_session, tenant, *, days_ago=0, grace=14):
    set_security_key_requirement(db_session, tenant.id, required=True, grace_days=grace)
    tenant.security_key_required_since = datetime.now(timezone.utc) - timedelta(days=days_ago)
    db_session.commit()


def _sign_in(db_session, tenant, user):
    return authenticate_password(db_session, tenant.slug, user.email, TenantFactory.PASSWORD)


@pytest.mark.asvs("6.3.3")
def test_after_the_grace_period_a_password_sign_in_needs_a_key(db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="key-req-enforced")
    _require(db_session, tenant, days_ago=15, grace=14)

    result = _sign_in(db_session, tenant, users["viewer"])

    assert result.outcome == PasswordLoginOutcome.SUCCESS
    assert result.identity.mfa_required and result.identity.needs_webauthn_enrollment  # registers one as part of signing in


def test_during_the_grace_period_someone_without_a_key_still_signs_in(db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="key-req-grace")
    _require(db_session, tenant, days_ago=3, grace=14)

    result = _sign_in(db_session, tenant, users["viewer"])

    assert not result.identity.mfa_required
    req = requirement(db_session, tenant, _membership(db_session, tenant, users["viewer"]))
    assert req.required and not req.enforced and req.grace_ends > datetime.now(timezone.utc)


def test_someone_with_a_key_always_uses_it(db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="key-req-has-key")
    _require(db_session, tenant, days_ago=1)
    db_session.add(WebauthnCredential(tenant_id=tenant.id, user_id=users["viewer"].id, credential_id=uuid.uuid4().bytes,
                                      public_key=b"k", sign_count=0))
    db_session.commit()
    result = _sign_in(db_session, tenant, users["viewer"])
    assert result.identity.mfa_required and not result.identity.needs_webauthn_enrollment


def test_a_customer_can_require_keys_for_its_own_people(db_session, tenant_factory):
    from openclear.services import security_group_service, user_service

    tenant, _account, _users = tenant_factory.make(slug="key-req-customer")
    customer = customer_service.create_customer(db_session, tenant.id, customer_service.CustomerInput(customer_number="C-1", name="Acme"))
    group = security_group_service.get_security_group_by_name(db_session, tenant.id, "Viewer")
    person = user_service.create_user_with_membership(db_session, tenant.id, email="clerk@acme-key.example.com",
                                                      password=TenantFactory.PASSWORD, security_group_id=group.id,
                                                      customer_id=customer.id)
    customer.require_security_key = True
    customer.security_key_required_since = datetime.now(timezone.utc) - timedelta(days=30)
    db_session.commit()

    assert requirement(db_session, tenant, _membership(db_session, tenant, person)).enforced
    assert not requirement(db_session, tenant, _membership(db_session, tenant, _users["viewer"])).required


def test_the_grace_period_banner_shows_until_a_key_is_added(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="key-req-banner")
    _require(db_session, tenant, days_ago=4, grace=14)
    client.get("/ui/login")
    client.post("/ui/login", data={"tenant_slug": tenant.slug, "email": users["viewer"].email, "password": TenantFactory.PASSWORD,
                                   "csrf_token": client.cookies.get("csrf_token"), "next": "/ui/"})
    page = client.get("/ui/exceptions")
    assert "data-security-key-due" in page.text and "10 days left" in page.text


def test_turning_it_on_applies_at_once_and_off_waits_for_a_second_admin(db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="key-req-dual")
    on = change_request_service.request_or_apply(
        db_session, "security_keys", {"values": {"require_security_key": True, "security_key_grace_days": 14}},
        tenant_id=tenant.id, user_id=users["admin"].id)
    db_session.commit()
    assert on.applied and tenant.require_security_key and tenant.security_key_required_since is not None

    off = change_request_service.request_or_apply(
        db_session, "security_keys", {"values": {"require_security_key": False, "security_key_grace_days": 14}},
        tenant_id=tenant.id, user_id=users["admin"].id)
    assert not off.applied and tenant.require_security_key  # a loosening: waits


def test_the_control_check_warns_until_required_and_in_force(db_session, tenant_factory):
    from openclear.services import control_checks

    tenant, _account, _users = tenant_factory.make(slug="key-req-check")
    assert control_checks.security_key_required(db_session, tenant.id).status == "WARN"
    _require(db_session, tenant, days_ago=1)
    assert "grace period" in control_checks.security_key_required(db_session, tenant.id).detail
    tenant.security_key_required_since = datetime.now(timezone.utc) - timedelta(days=30)
    db_session.commit()
    assert control_checks.security_key_required(db_session, tenant.id).status == "PASS"


def test_the_bank_can_turn_it_on_for_a_customer_from_its_page(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="key-req-customer-page")
    customer = customer_service.create_customer(db_session, tenant.id, customer_service.CustomerInput(customer_number="C-9", name="Beta"))
    db_session.commit()
    client.get("/ui/login")
    client.post("/ui/login", data={"tenant_slug": tenant.slug, "email": users["admin"].email, "password": TenantFactory.PASSWORD,
                                   "csrf_token": client.cookies.get("csrf_token"), "next": "/ui/"})
    page = client.get(f"/ui/customers/{customer.id}/password-policy")
    assert "Require a security key or passkey for our people" in page.text
    client.post(f"/ui/customers/{customer.id}/password-policy/security-keys",
                data={"csrf_token": client.cookies.get("csrf_token"), "require_security_key": "true"})
    db_session.refresh(customer)
    assert customer.require_security_key and customer.security_key_required_since is not None
