# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import pytest

from openclear.auth.oidc_service import OidcClaims
from openclear.domain.sso_connection import SsoProvider
from openclear.repositories.tenant_membership_repo import TenantMembershipRepository
from openclear.services import security_group_service, sso_service


def _connection_input(**overrides):
    from openclear.services.sso_service import SsoConnectionInput

    defaults = dict(
        provider=SsoProvider.OKTA,
        display_name="Test Okta",
        issuer="https://idp.example.com",
        client_id="client-1",
        client_secret="hunter2-secret",
        groups_claim_name="groups",
        auto_provision=False,
        customer_id=None,
    )
    defaults.update(overrides)
    return SsoConnectionInput(**defaults)


def test_create_connection_encrypts_secret_at_rest(db_session, tenant_factory):
    tenant, _account, _users = tenant_factory.make(slug="sso-create")

    connection = sso_service.create_connection(db_session, tenant.id, _connection_input())
    db_session.commit()

    assert connection.client_secret_encrypted != "hunter2-secret"
    from openclear.auth.crypto import decrypt_secret

    assert decrypt_secret(connection.client_secret_encrypted) == "hunter2-secret"


def test_create_connection_requires_client_secret(db_session, tenant_factory):
    tenant, _account, _users = tenant_factory.make(slug="sso-create-nosecret")

    with pytest.raises(ValueError, match="Client secret"):
        sso_service.create_connection(db_session, tenant.id, _connection_input(client_secret=""))


def test_update_connection_blank_secret_keeps_existing(db_session, tenant_factory):
    tenant, _account, _users = tenant_factory.make(slug="sso-update-keep-secret")
    connection = sso_service.create_connection(db_session, tenant.id, _connection_input())
    db_session.commit()
    original_encrypted = connection.client_secret_encrypted

    sso_service.update_connection(db_session, tenant.id, connection.id, _connection_input(client_secret="", display_name="Renamed"))
    db_session.commit()

    assert connection.display_name == "Renamed"
    assert connection.client_secret_encrypted == original_encrypted


def test_get_login_connections_excludes_connections_without_mappings(db_session, tenant_factory):
    tenant, _account, _users = tenant_factory.make(slug="sso-login-conns")
    connection = sso_service.create_connection(db_session, tenant.id, _connection_input())
    db_session.commit()

    assert sso_service.get_login_connections(db_session, tenant.id) == []

    group = security_group_service.get_security_group_by_name(db_session, tenant.id, "Viewer")
    sso_service.add_group_mapping(db_session, tenant.id, connection.id, external_group="ext-viewers", security_group_id=group.id)
    db_session.commit()

    assert [c.id for c in sso_service.get_login_connections(db_session, tenant.id)] == [connection.id]


def test_get_login_connections_excludes_inactive(db_session, tenant_factory):
    tenant, _account, _users = tenant_factory.make(slug="sso-login-inactive")
    connection = sso_service.create_connection(db_session, tenant.id, _connection_input())
    group = security_group_service.get_security_group_by_name(db_session, tenant.id, "Viewer")
    sso_service.add_group_mapping(db_session, tenant.id, connection.id, external_group="ext", security_group_id=group.id)
    sso_service.deactivate_connection(db_session, tenant.id, connection.id)
    db_session.commit()

    assert sso_service.get_login_connections(db_session, tenant.id) == []


def test_get_login_connections_scoped_to_customer(db_session, tenant_factory):
    from openclear.services import customer_service

    tenant, _account, _users = tenant_factory.make(slug="sso-login-scope")
    customer = customer_service.create_customer(db_session, tenant.id, customer_service.CustomerInput(customer_number="C-1", name="Acme"))
    bank_connection = sso_service.create_connection(db_session, tenant.id, _connection_input())
    customer_connection = sso_service.create_connection(db_session, tenant.id, _connection_input(customer_id=customer.id, display_name="Customer Okta"))
    group = security_group_service.get_security_group_by_name(db_session, tenant.id, "Viewer")
    sso_service.add_group_mapping(db_session, tenant.id, bank_connection.id, external_group="ext", security_group_id=group.id)
    sso_service.add_group_mapping(db_session, tenant.id, customer_connection.id, external_group="ext", security_group_id=group.id)
    db_session.commit()

    assert [c.id for c in sso_service.get_login_connections(db_session, tenant.id, customer_id=None)] == [bank_connection.id]
    assert [c.id for c in sso_service.get_login_connections(db_session, tenant.id, customer_id=customer.id)] == [customer_connection.id]


def _make_mapped_connection(db_session, tenant, *, customer_id=None, auto_provision=False, external_group="openclear-users", group_name="Viewer"):
    connection = sso_service.create_connection(db_session, tenant.id, _connection_input(customer_id=customer_id, auto_provision=auto_provision))
    group = security_group_service.get_security_group_by_name(db_session, tenant.id, group_name)
    sso_service.add_group_mapping(db_session, tenant.id, connection.id, external_group=external_group, security_group_id=group.id)
    db_session.commit()
    return connection, group


def test_complete_sso_login_refuses_when_no_group_matches(db_session, tenant_factory):
    tenant, _account, _users = tenant_factory.make(slug="sso-login-nomatch")
    connection, _group = _make_mapped_connection(db_session, tenant, auto_provision=True)
    claims = OidcClaims(email="newperson@example.com", subject="sub-1", groups=["some-other-group"])

    result = sso_service.complete_sso_login(db_session, tenant.id, connection, claims)

    assert result.outcome == "not_authorized"
    from openclear.repositories.user_repo import UserRepository

    assert UserRepository(db_session).get_by_email("newperson@example.com") is None


def test_complete_sso_login_auto_provisions_new_user(db_session, tenant_factory):
    tenant, _account, _users = tenant_factory.make(slug="sso-login-autoprov")
    connection, group = _make_mapped_connection(db_session, tenant, auto_provision=True)
    claims = OidcClaims(email="newperson@example.com", subject="sub-1", groups=["openclear-users"])

    result = sso_service.complete_sso_login(db_session, tenant.id, connection, claims)
    db_session.commit()

    assert result.outcome == "success"
    assert result.user.email == "newperson@example.com"
    assert result.membership.security_group_id == group.id
    assert result.membership.customer_id is None


def test_complete_sso_login_without_auto_provision_refuses_new_user(db_session, tenant_factory):
    tenant, _account, _users = tenant_factory.make(slug="sso-login-noautoprov")
    connection, _group = _make_mapped_connection(db_session, tenant, auto_provision=False)
    claims = OidcClaims(email="newperson@example.com", subject="sub-1", groups=["openclear-users"])

    result = sso_service.complete_sso_login(db_session, tenant.id, connection, claims)

    assert result.outcome == "not_provisioned"


def test_complete_sso_login_syncs_security_group_for_returning_user(db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="sso-login-sync")
    connection, _viewer_group = _make_mapped_connection(db_session, tenant, auto_provision=True, external_group="openclear-approvers", group_name="Approver")
    claims = OidcClaims(email=users["viewer"].email, subject="sub-1", groups=["openclear-approvers"])

    membership_before = [m for m in TenantMembershipRepository(db_session, tenant.id).list(user_id=users["viewer"].id) if m.customer_id is None][0]
    approver_group = security_group_service.get_security_group_by_name(db_session, tenant.id, "Approver")
    assert membership_before.security_group_id != approver_group.id

    result = sso_service.complete_sso_login(db_session, tenant.id, connection, claims)
    db_session.commit()

    assert result.outcome == "success"
    assert result.membership.security_group_id == approver_group.id


def test_complete_sso_login_deactivates_membership_when_group_no_longer_matches(db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="sso-login-revoke")
    connection, _group = _make_mapped_connection(db_session, tenant, auto_provision=True, external_group="openclear-viewers", group_name="Viewer")
    claims_ok = OidcClaims(email=users["viewer"].email, subject="sub-1", groups=["openclear-viewers"])
    result = sso_service.complete_sso_login(db_session, tenant.id, connection, claims_ok)
    db_session.commit()
    assert result.outcome == "success"
    assert result.membership.is_active is True

    # Same user logs in again, but their IdP groups no longer include the mapped one.
    claims_revoked = OidcClaims(email=users["viewer"].email, subject="sub-1", groups=["some-unrelated-group"])
    result2 = sso_service.complete_sso_login(db_session, tenant.id, connection, claims_revoked)
    db_session.commit()

    assert result2.outcome == "not_authorized"
    db_session.expire_all()
    membership_after = [m for m in TenantMembershipRepository(db_session, tenant.id).list(user_id=users["viewer"].id) if m.customer_id is None][0]
    assert membership_after.is_active is False


def test_complete_sso_login_priority_tie_break(db_session, tenant_factory):
    tenant, _account, _users = tenant_factory.make(slug="sso-login-priority")
    connection = sso_service.create_connection(db_session, tenant.id, _connection_input(auto_provision=True))
    admin_group = security_group_service.get_security_group_by_name(db_session, tenant.id, "Admin")
    viewer_group = security_group_service.get_security_group_by_name(db_session, tenant.id, "Viewer")
    sso_service.add_group_mapping(db_session, tenant.id, connection.id, external_group="g1", security_group_id=viewer_group.id, priority=50)
    sso_service.add_group_mapping(db_session, tenant.id, connection.id, external_group="g2", security_group_id=admin_group.id, priority=10)
    db_session.commit()

    claims = OidcClaims(email="person@example.com", subject="s", groups=["g1", "g2"])
    result = sso_service.complete_sso_login(db_session, tenant.id, connection, claims)
    db_session.commit()

    assert result.outcome == "success"
    assert result.membership.security_group_id == admin_group.id  # priority 10 beats 50


def test_set_tenant_password_login_enabled_guards_against_lockout(db_session, tenant_factory):
    tenant, _account, _users = tenant_factory.make(slug="sso-lockout-tenant")

    with pytest.raises(ValueError, match="Cannot require SSO"):
        sso_service.set_tenant_password_login_enabled(db_session, tenant.id, enabled=False)

    connection, _group = _make_mapped_connection(db_session, tenant)
    sso_service.set_tenant_password_login_enabled(db_session, tenant.id, enabled=False)
    db_session.commit()

    from openclear.domain.tenant import Tenant

    assert db_session.get(Tenant, tenant.id).password_login_enabled is False


def test_set_customer_password_login_enabled_guards_against_lockout(db_session, tenant_factory):
    from openclear.services import customer_service

    tenant, _account, _users = tenant_factory.make(slug="sso-lockout-customer")
    customer = customer_service.create_customer(db_session, tenant.id, customer_service.CustomerInput(customer_number="C-1", name="Acme"))
    db_session.commit()

    with pytest.raises(ValueError, match="Cannot require SSO"):
        sso_service.set_customer_password_login_enabled(db_session, tenant.id, customer.id, enabled=False)

    _connection, _group = _make_mapped_connection(db_session, tenant, customer_id=customer.id)
    sso_service.set_customer_password_login_enabled(db_session, tenant.id, customer.id, enabled=False)
    db_session.commit()

    db_session.expire_all()
    assert customer_service.get_customer(db_session, tenant.id, customer.id).password_login_enabled is False
