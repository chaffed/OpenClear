# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import uuid

import pytest

from openclear.repositories.tenant_membership_repo import TenantMembershipRepository
from openclear.repositories.user_repo import UserRepository
from openclear.services import customer_service, security_group_service, tenant_service, user_service


def test_add_user_creates_brand_new_identity(db_session, tenant_factory):
    tenant, _account, _users = tenant_factory.make(slug="user-add-new")
    group = security_group_service.get_security_group_by_name(db_session, tenant.id, "Preparer")

    result = user_service.add_user(db_session, tenant.id, email="new@example.com", password="hunter2-hunter2", security_group_id=group.id)
    db_session.commit()

    assert result.outcome == "created"
    user = UserRepository(db_session).get_by_email("new@example.com")
    assert user is not None
    memberships = TenantMembershipRepository(db_session, tenant.id).list(user_id=user.id)
    assert len(memberships) == 1
    assert memberships[0].security_group_id == group.id


def test_add_user_without_password_fails_for_new_email(db_session, tenant_factory):
    tenant, _account, _users = tenant_factory.make(slug="user-add-no-password")
    group = security_group_service.get_security_group_by_name(db_session, tenant.id, "Preparer")

    result = user_service.add_user(db_session, tenant.id, email="nopassword@example.com", password="", security_group_id=group.id)

    assert result.outcome == "failed"
    assert UserRepository(db_session).get_by_email("nopassword@example.com") is None


def test_add_user_existing_email_in_other_tenant_needs_confirmation(db_session, tenant_factory):
    tenant_a, _account_a, users_a = tenant_factory.make(slug="user-cross-a")
    tenant_b, _account_b, _users_b = tenant_factory.make(slug="user-cross-b")
    group_b = security_group_service.get_security_group_by_name(db_session, tenant_b.id, "Viewer")

    existing_email = users_a["preparer"].email
    result = user_service.add_user(db_session, tenant_b.id, email=existing_email, password="", security_group_id=group_b.id)

    assert result.outcome == "needs_confirmation"
    # nothing was created — no membership in tenant_b for this user yet
    assert TenantMembershipRepository(db_session, tenant_b.id).list(user_id=users_a["preparer"].id) == []


def test_add_user_already_a_member_is_idempotent(db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="user-already-member")
    group = security_group_service.get_security_group_by_name(db_session, tenant.id, "Admin")

    result = user_service.add_user(db_session, tenant.id, email=users["admin"].email, password="", security_group_id=group.id)

    assert result.outcome == "already_member"


def test_confirm_cross_tenant_membership_re_resolves_by_email(db_session, tenant_factory):
    tenant_a, _account_a, users_a = tenant_factory.make(slug="user-confirm-a")
    tenant_b, _account_b, _users_b = tenant_factory.make(slug="user-confirm-b")
    group_b = security_group_service.get_security_group_by_name(db_session, tenant_b.id, "Viewer")

    membership = user_service.confirm_cross_tenant_membership(
        db_session, tenant_b.id, email=users_a["preparer"].email, security_group_id=group_b.id
    )
    db_session.commit()

    assert membership is not None
    assert membership.user_id == users_a["preparer"].id
    assert membership.tenant_id == tenant_b.id
    assert membership.security_group_id == group_b.id

    # same identity, same password, now works in both tenants
    memberships_a = TenantMembershipRepository(db_session, tenant_a.id).list(user_id=users_a["preparer"].id)
    memberships_b = TenantMembershipRepository(db_session, tenant_b.id).list(user_id=users_a["preparer"].id)
    assert len(memberships_a) == 1
    assert len(memberships_b) == 1


def test_confirm_cross_tenant_membership_unknown_email_returns_none(db_session, tenant_factory):
    tenant, _account, _users = tenant_factory.make(slug="user-confirm-unknown")
    group = security_group_service.get_security_group_by_name(db_session, tenant.id, "Viewer")

    assert user_service.confirm_cross_tenant_membership(db_session, tenant.id, email="ghost@example.com", security_group_id=group.id) is None


def test_deactivate_and_reactivate_membership(db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="user-deactivate")
    membership = TenantMembershipRepository(db_session, tenant.id).list(user_id=users["viewer"].id)[0]

    deactivated = user_service.deactivate_membership(db_session, tenant.id, membership.id)
    db_session.commit()
    assert deactivated.is_active is False

    reactivated = user_service.reactivate_membership(db_session, tenant.id, membership.id)
    db_session.commit()
    assert reactivated.is_active is True


def test_list_memberships_for_user_across_tenants(db_session, tenant_factory):
    tenant_a, _account_a, users_a = tenant_factory.make(slug="user-list-memberships-a")
    tenant_b, _account_b, _users_b = tenant_factory.make(slug="user-list-memberships-b")
    group_b = security_group_service.get_security_group_by_name(db_session, tenant_b.id, "Viewer")
    user_service.confirm_cross_tenant_membership(db_session, tenant_b.id, email=users_a["admin"].email, security_group_id=group_b.id)
    db_session.commit()

    memberships = user_service.list_memberships_for_user(db_session, users_a["admin"].id)
    tenant_ids = {m.tenant.id for m in memberships}
    assert tenant_ids == {tenant_a.id, tenant_b.id}


def test_create_users_from_rows_bulk(db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="user-bulk-rows")
    tenant_other, _account_other, users_other = tenant_factory.make(slug="user-bulk-rows-other")

    rows = [
        {"email": "brandnew@example.com", "security_group": "Preparer", "password": "hunter2-hunter2"},
        {"email": users_other["viewer"].email, "security_group": "Viewer", "password": ""},
        {"email": "missinggroup@example.com", "security_group": "Nonexistent", "password": "hunter2-hunter2"},
    ]

    results = user_service.create_users_from_rows(db_session, tenant.id, rows)

    assert results[0].outcome == "created"
    assert results[1].outcome == "needs_confirmation"
    assert results[2].outcome == "failed"
    assert "No security group found" in results[2].error


def test_update_membership_changes_security_group_and_customer(db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="user-update-membership")
    membership = TenantMembershipRepository(db_session, tenant.id).list(user_id=users["viewer"].id)[0]
    approver_group = security_group_service.get_security_group_by_name(db_session, tenant.id, "Approver")
    customer = customer_service.create_customer(
        db_session, tenant.id, customer_service.CustomerInput(customer_number="C-1", name="Acme Co")
    )
    db_session.commit()

    updated = user_service.update_membership(
        db_session, tenant.id, membership.id, security_group_id=approver_group.id, customer_id=customer.id
    )
    db_session.commit()

    assert updated.security_group_id == approver_group.id
    assert updated.customer_id == customer.id


def test_update_membership_back_to_bank_wide(db_session, tenant_factory):
    tenant, _account, _users = tenant_factory.make(slug="user-update-membership-bankwide")
    customer = customer_service.create_customer(
        db_session, tenant.id, customer_service.CustomerInput(customer_number="C-1", name="Acme Co")
    )
    preparer_group = security_group_service.get_security_group_by_name(db_session, tenant.id, "Preparer")
    # a brand-new identity with ONLY this one customer-scoped membership -- no other
    # membership in this tenant to collide with when moving it to bank-wide
    scoped_user = user_service.create_user_with_membership(
        db_session, tenant.id, email="scoped-only@example.com", password="hunter2-hunter2",
        security_group_id=preparer_group.id, customer_id=customer.id,
    )
    membership = TenantMembershipRepository(db_session, tenant.id).list(user_id=scoped_user.id)[0]
    db_session.commit()

    updated = user_service.update_membership(
        db_session, tenant.id, membership.id, security_group_id=preparer_group.id, customer_id=None
    )
    db_session.commit()

    assert updated.customer_id is None


def test_update_membership_raises_for_unknown_membership(db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="user-update-membership-unknown")
    group = security_group_service.get_security_group_by_name(db_session, tenant.id, "Preparer")

    with pytest.raises(ValueError, match="Membership not found"):
        user_service.update_membership(db_session, tenant.id, uuid.uuid4(), security_group_id=group.id, customer_id=None)


def test_update_membership_raises_for_other_tenants_membership(db_session, tenant_factory):
    tenant_a, _account_a, users_a = tenant_factory.make(slug="user-update-membership-cross-a")
    tenant_b, _account_b, _users_b = tenant_factory.make(slug="user-update-membership-cross-b")
    membership = TenantMembershipRepository(db_session, tenant_a.id).list(user_id=users_a["viewer"].id)[0]
    group_b = security_group_service.get_security_group_by_name(db_session, tenant_b.id, "Preparer")

    with pytest.raises(ValueError, match="Membership not found"):
        user_service.update_membership(db_session, tenant_b.id, membership.id, security_group_id=group_b.id, customer_id=None)


def test_update_membership_raises_for_unknown_security_group(db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="user-update-membership-badgroup")
    membership = TenantMembershipRepository(db_session, tenant.id).list(user_id=users["viewer"].id)[0]

    with pytest.raises(ValueError, match="Security group not found"):
        user_service.update_membership(db_session, tenant.id, membership.id, security_group_id=uuid.uuid4(), customer_id=None)


def test_update_membership_raises_for_unknown_customer(db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="user-update-membership-badcustomer")
    membership = TenantMembershipRepository(db_session, tenant.id).list(user_id=users["viewer"].id)[0]
    group = security_group_service.get_security_group_by_name(db_session, tenant.id, "Preparer")

    with pytest.raises(ValueError, match="Customer not found"):
        user_service.update_membership(db_session, tenant.id, membership.id, security_group_id=group.id, customer_id=uuid.uuid4())


def test_update_membership_raises_on_scope_collision(db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="user-update-membership-collision")
    customer = customer_service.create_customer(
        db_session, tenant.id, customer_service.CustomerInput(customer_number="C-1", name="Acme Co")
    )
    preparer_group = security_group_service.get_security_group_by_name(db_session, tenant.id, "Preparer")
    # viewer now holds TWO memberships in this tenant: their original bank-wide one, and a new customer-scoped one
    scoped_membership = user_service.confirm_cross_tenant_membership(
        db_session, tenant.id, email=users["viewer"].email, security_group_id=preparer_group.id, customer_id=customer.id
    )
    db_session.commit()

    # trying to move the scoped membership back to bank-wide collides with the viewer's existing bank-wide membership
    with pytest.raises(ValueError, match="already has a separate membership scoped to that customer"):
        user_service.update_membership(
            db_session, tenant.id, scoped_membership.id, security_group_id=preparer_group.id, customer_id=None
        )


def test_get_access_for_email_scoped_to_this_tenant_only(db_session, tenant_factory):
    tenant_a, _account_a, users_a = tenant_factory.make(slug="user-access-a")
    tenant_b, _account_b, _users_b = tenant_factory.make(slug="user-access-b")
    group_b = security_group_service.get_security_group_by_name(db_session, tenant_b.id, "Viewer")
    user_service.confirm_cross_tenant_membership(db_session, tenant_b.id, email=users_a["admin"].email, security_group_id=group_b.id)
    db_session.commit()

    rows_a = user_service.get_access_for_email(db_session, tenant_a.id, users_a["admin"].email)
    rows_b = user_service.get_access_for_email(db_session, tenant_b.id, users_a["admin"].email)

    assert len(rows_a) == 1
    assert len(rows_b) == 1
    # tenant_a's lookup never sees anything about the user's membership in tenant_b, and vice versa
    assert rows_a[0].security_group_name == "Admin"
    assert rows_b[0].security_group_name == "Viewer"


def test_get_access_for_email_unknown_email_returns_empty(db_session, tenant_factory):
    tenant, _account, _users = tenant_factory.make(slug="user-access-unknown")

    assert user_service.get_access_for_email(db_session, tenant.id, "ghost@example.com") == []


def test_grant_multi_customer_access_direct_grant_across_several_customers(db_session, tenant_factory):
    tenant, _account, _users = tenant_factory.make(slug="user-grant-multi")
    customer_1 = customer_service.create_customer(db_session, tenant.id, customer_service.CustomerInput(customer_number="C-1", name="Acme"))
    customer_2 = customer_service.create_customer(db_session, tenant.id, customer_service.CustomerInput(customer_number="C-2", name="Beta"))
    group = security_group_service.get_security_group_by_name(db_session, tenant.id, "Bookkeeper")

    pairs = user_service.grant_multi_customer_access(
        db_session,
        tenant.id,
        email="bookkeeper@example.com",
        password="hunter2-hunter2",
        security_group_id=group.id,
        customer_ids=[None, customer_1.id, customer_2.id],
    )
    db_session.commit()

    assert [outcome for _cid, outcome in [(c, r.outcome) for c, r in pairs]] == ["created", "created", "created"]
    user = UserRepository(db_session).get_by_email("bookkeeper@example.com")
    memberships = TenantMembershipRepository(db_session, tenant.id).list(user_id=user.id)
    assert {m.customer_id for m in memberships} == {None, customer_1.id, customer_2.id}
    assert all(m.security_group_id == group.id for m in memberships)


def test_grant_multi_customer_access_cross_tenant_email_needs_confirmation(db_session, tenant_factory):
    tenant_a, _account_a, users_a = tenant_factory.make(slug="user-grant-cross-a")
    tenant_b, _account_b, _users_b = tenant_factory.make(slug="user-grant-cross-b")
    customer_1 = customer_service.create_customer(db_session, tenant_b.id, customer_service.CustomerInput(customer_number="C-1", name="Acme"))
    customer_2 = customer_service.create_customer(db_session, tenant_b.id, customer_service.CustomerInput(customer_number="C-2", name="Beta"))
    group_b = security_group_service.get_security_group_by_name(db_session, tenant_b.id, "Bookkeeper")

    pairs = user_service.grant_multi_customer_access(
        db_session,
        tenant_b.id,
        email=users_a["preparer"].email,
        password="",
        security_group_id=group_b.id,
        customer_ids=[customer_1.id, customer_2.id],
    )

    assert [result.outcome for _cid, result in pairs] == ["needs_confirmation", "needs_confirmation"]
    assert TenantMembershipRepository(db_session, tenant_b.id).list(user_id=users_a["preparer"].id) == []


# --- Password policy enforcement ---


def test_create_user_with_membership_rejects_password_below_tenant_policy(db_session, tenant_factory):
    tenant, _account, _users = tenant_factory.make(slug="user-password-policy-reject")
    tenant_service.set_password_policy(
        db_session, tenant.id, min_length=12, require_uppercase=True, require_lowercase=False,
        require_number=False, require_symbol=False,
    )
    db_session.commit()
    group = security_group_service.get_security_group_by_name(db_session, tenant.id, "Preparer")

    with pytest.raises(ValueError, match="at least 12 characters"):
        user_service.create_user_with_membership(
            db_session, tenant.id, email="weakpass@example.com", password="short", security_group_id=group.id
        )
    assert UserRepository(db_session).get_by_email("weakpass@example.com") is None


def test_create_user_with_membership_accepts_password_meeting_tenant_policy(db_session, tenant_factory):
    tenant, _account, _users = tenant_factory.make(slug="user-password-policy-accept")
    tenant_service.set_password_policy(
        db_session, tenant.id, min_length=12, require_uppercase=True, require_lowercase=True,
        require_number=True, require_symbol=False,
    )
    db_session.commit()
    group = security_group_service.get_security_group_by_name(db_session, tenant.id, "Preparer")

    user = user_service.create_user_with_membership(
        db_session, tenant.id, email="strongpass@example.com", password="Str0ngPassword", security_group_id=group.id
    )
    db_session.commit()

    assert UserRepository(db_session).get_by_email("strongpass@example.com").id == user.id


def test_add_user_returns_failed_outcome_for_password_policy_violation(db_session, tenant_factory):
    """add_user (the router-facing entry point) must convert the ValueError
    create_user_with_membership raises into the same AddUserResult(outcome="failed")
    shape every other failure already uses -- not an uncaught exception."""
    tenant, _account, _users = tenant_factory.make(slug="user-password-policy-add-user")
    tenant_service.set_password_policy(
        db_session, tenant.id, min_length=20, require_uppercase=False, require_lowercase=False,
        require_number=False, require_symbol=False,
    )
    db_session.commit()
    group = security_group_service.get_security_group_by_name(db_session, tenant.id, "Preparer")

    result = user_service.add_user(
        db_session, tenant.id, email="tooshort@example.com", password="short-password", security_group_id=group.id
    )

    assert result.outcome == "failed"
    assert "at least 20 characters" in result.error
    assert UserRepository(db_session).get_by_email("tooshort@example.com") is None


def test_create_user_with_membership_enforces_customer_stricter_policy(db_session, tenant_factory):
    tenant, _account, _users = tenant_factory.make(slug="user-password-policy-customer")
    tenant_service.set_password_policy(
        db_session, tenant.id, min_length=8, require_uppercase=False, require_lowercase=False,
        require_number=False, require_symbol=False,
    )
    customer = customer_service.create_customer(
        db_session, tenant.id, customer_service.CustomerInput(customer_number="C-1", name="Acme")
    )
    customer_service.set_password_policy(
        db_session, tenant.id, customer.id, min_length=None, require_uppercase=True, require_lowercase=False,
        require_number=False, require_symbol=False,
    )
    db_session.commit()
    group = security_group_service.get_security_group_by_name(db_session, tenant.id, "Preparer")

    # Meets the tenant's own (permissive) policy but not this customer's additional
    # uppercase requirement -- and a bank-wide (customer_id=None) user with the exact
    # same password is unaffected, since the customer's stricter rule only applies within
    # that customer's own scope.
    with pytest.raises(ValueError, match="uppercase"):
        user_service.create_user_with_membership(
            db_session, tenant.id, email="scoped@example.com", password="lowercase-only-1",
            security_group_id=group.id, customer_id=customer.id,
        )

    user = user_service.create_user_with_membership(
        db_session, tenant.id, email="bankwide@example.com", password="lowercase-only-1", security_group_id=group.id
    )
    db_session.commit()
    assert UserRepository(db_session).get_by_email("bankwide@example.com").id == user.id
