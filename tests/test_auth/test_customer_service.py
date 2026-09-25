# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import pytest

from openclear.services import customer_service, tenant_service


def test_create_customer(db_session, tenant_factory):
    tenant, _account, _users = tenant_factory.make(slug="cust-svc-create")

    customer = customer_service.create_customer(
        db_session, tenant.id, customer_service.CustomerInput(customer_number="C-100", name="Acme Corp")
    )
    db_session.commit()

    assert customer.tenant_id == tenant.id
    assert customer.customer_number == "C-100"
    assert customer.name == "Acme Corp"
    assert customer.is_active is True


def test_list_customers_returns_only_this_tenants_customers(db_session, tenant_factory):
    tenant_a, _account_a, _users_a = tenant_factory.make(slug="cust-svc-list-a")
    tenant_b, _account_b, _users_b = tenant_factory.make(slug="cust-svc-list-b")

    customer_service.create_customer(db_session, tenant_a.id, customer_service.CustomerInput(customer_number="A-1", name="A One"))
    customer_service.create_customer(db_session, tenant_a.id, customer_service.CustomerInput(customer_number="A-2", name="A Two"))
    customer_service.create_customer(db_session, tenant_b.id, customer_service.CustomerInput(customer_number="B-1", name="B One"))
    db_session.commit()

    customers_a = customer_service.list_customers(db_session, tenant_a.id)
    assert {c.customer_number for c in customers_a} == {"A-1", "A-2"}

    customers_b = customer_service.list_customers(db_session, tenant_b.id)
    assert {c.customer_number for c in customers_b} == {"B-1"}


def test_get_customer_by_id(db_session, tenant_factory):
    tenant, _account, _users = tenant_factory.make(slug="cust-svc-get")
    customer = customer_service.create_customer(
        db_session, tenant.id, customer_service.CustomerInput(customer_number="C-1", name="Foo Inc")
    )
    db_session.commit()

    found = customer_service.get_customer(db_session, tenant.id, customer.id)
    assert found is not None
    assert found.id == customer.id


def test_get_customer_from_another_tenant_returns_none(db_session, tenant_factory):
    tenant_a, _account_a, _users_a = tenant_factory.make(slug="cust-svc-get-a")
    tenant_b, _account_b, _users_b = tenant_factory.make(slug="cust-svc-get-b")
    customer = customer_service.create_customer(
        db_session, tenant_a.id, customer_service.CustomerInput(customer_number="C-1", name="Foo Inc")
    )
    db_session.commit()

    assert customer_service.get_customer(db_session, tenant_b.id, customer.id) is None


def test_get_customer_by_number(db_session, tenant_factory):
    tenant, _account, _users = tenant_factory.make(slug="cust-svc-get-number")
    customer = customer_service.create_customer(
        db_session, tenant.id, customer_service.CustomerInput(customer_number="C-42", name="Bar LLC")
    )
    db_session.commit()

    found = customer_service.get_customer_by_number(db_session, tenant.id, "C-42")
    assert found is not None
    assert found.id == customer.id

    assert customer_service.get_customer_by_number(db_session, tenant.id, "does-not-exist") is None


def test_get_customer_by_number_scoped_to_tenant(db_session, tenant_factory):
    tenant_a, _account_a, _users_a = tenant_factory.make(slug="cust-svc-number-scope-a")
    tenant_b, _account_b, _users_b = tenant_factory.make(slug="cust-svc-number-scope-b")
    customer_service.create_customer(db_session, tenant_a.id, customer_service.CustomerInput(customer_number="SAME", name="A Corp"))
    customer_b = customer_service.create_customer(
        db_session, tenant_b.id, customer_service.CustomerInput(customer_number="SAME", name="B Corp")
    )
    db_session.commit()

    found = customer_service.get_customer_by_number(db_session, tenant_b.id, "SAME")
    assert found is not None
    assert found.id == customer_b.id
    assert found.name == "B Corp"


def test_create_customer_with_contact_details(db_session, tenant_factory):
    tenant, _account, _users = tenant_factory.make(slug="cust-svc-contact-create")

    customer = customer_service.create_customer(
        db_session,
        tenant.id,
        customer_service.CustomerInput(
            customer_number="C-200",
            name="Widgets Inc",
            external_customer_id="CIF-9001",
            tax_id="12-3456789",
            primary_contact_name="Jane Doe",
            email="jane@widgets.example.com",
            phone="555-0100",
            website="https://widgets.example.com",
            address_line1="123 Main St",
            address_line2="Suite 400",
            city="Springfield",
            state="IL",
            postal_code="62701",
            notes="Prefers email contact.",
        ),
    )
    db_session.commit()

    assert customer.external_customer_id == "CIF-9001"
    assert customer.tax_id == "12-3456789"
    assert customer.primary_contact_name == "Jane Doe"
    assert customer.email == "jane@widgets.example.com"
    assert customer.phone == "555-0100"
    assert customer.website == "https://widgets.example.com"
    assert customer.address_line1 == "123 Main St"
    assert customer.address_line2 == "Suite 400"
    assert customer.city == "Springfield"
    assert customer.state == "IL"
    assert customer.postal_code == "62701"
    assert customer.notes == "Prefers email contact."


def test_create_customer_contact_fields_default_to_none(db_session, tenant_factory):
    tenant, _account, _users = tenant_factory.make(slug="cust-svc-contact-default")

    customer = customer_service.create_customer(
        db_session, tenant.id, customer_service.CustomerInput(customer_number="C-201", name="No Contact Co")
    )
    db_session.commit()

    assert customer.external_customer_id is None
    assert customer.tax_id is None
    assert customer.email is None
    assert customer.notes is None


def test_update_customer_changes_fields(db_session, tenant_factory):
    tenant, _account, _users = tenant_factory.make(slug="cust-svc-update")
    customer = customer_service.create_customer(
        db_session, tenant.id, customer_service.CustomerInput(customer_number="C-300", name="Old Name", email="old@example.com")
    )
    db_session.commit()

    updated = customer_service.update_customer(
        db_session,
        tenant.id,
        customer.id,
        customer_service.CustomerInput(customer_number="C-300", name="New Name", email="new@example.com", phone="555-0199"),
    )
    db_session.commit()

    assert updated is not None
    assert updated.id == customer.id
    assert updated.name == "New Name"
    assert updated.email == "new@example.com"
    assert updated.phone == "555-0199"


def test_update_customer_can_clear_a_field(db_session, tenant_factory):
    tenant, _account, _users = tenant_factory.make(slug="cust-svc-update-clear")
    customer = customer_service.create_customer(
        db_session, tenant.id, customer_service.CustomerInput(customer_number="C-301", name="Has Email", email="a@example.com")
    )
    db_session.commit()

    updated = customer_service.update_customer(
        db_session, tenant.id, customer.id, customer_service.CustomerInput(customer_number="C-301", name="Has Email", email=None)
    )
    db_session.commit()

    assert updated.email is None


def test_update_customer_returns_none_for_unknown_or_other_tenant(db_session, tenant_factory):
    tenant_a, _account_a, _users_a = tenant_factory.make(slug="cust-svc-update-other-a")
    tenant_b, _account_b, _users_b = tenant_factory.make(slug="cust-svc-update-other-b")
    customer = customer_service.create_customer(
        db_session, tenant_a.id, customer_service.CustomerInput(customer_number="C-1", name="Foo Inc")
    )
    db_session.commit()

    result = customer_service.update_customer(
        db_session, tenant_b.id, customer.id, customer_service.CustomerInput(customer_number="C-1", name="Hacked Name")
    )
    assert result is None


def test_set_password_policy_allows_stricter_than_tenant(db_session, tenant_factory):
    tenant, _account, _users = tenant_factory.make(slug="cust-svc-password-policy-stricter")
    tenant_service.set_password_policy(
        db_session, tenant.id, min_length=8, require_uppercase=False, require_lowercase=False,
        require_number=False, require_symbol=False,
    )
    customer = customer_service.create_customer(
        db_session, tenant.id, customer_service.CustomerInput(customer_number="C-1", name="Acme")
    )
    db_session.commit()

    updated = customer_service.set_password_policy(
        db_session, tenant.id, customer.id, min_length=16, require_uppercase=True, require_lowercase=False,
        require_number=False, require_symbol=True,
    )
    db_session.commit()

    assert updated.password_min_length == 16
    assert updated.password_require_uppercase is True
    assert updated.password_require_symbol is True


def test_set_password_policy_rejects_min_length_below_tenant(db_session, tenant_factory):
    tenant, _account, _users = tenant_factory.make(slug="cust-svc-password-policy-weaker")
    tenant_service.set_password_policy(
        db_session, tenant.id, min_length=12, require_uppercase=False, require_lowercase=False,
        require_number=False, require_symbol=False,
    )
    customer = customer_service.create_customer(
        db_session, tenant.id, customer_service.CustomerInput(customer_number="C-1", name="Acme")
    )
    db_session.commit()

    with pytest.raises(ValueError, match="can't be less than the tenant's own minimum"):
        customer_service.set_password_policy(
            db_session, tenant.id, customer.id, min_length=6, require_uppercase=False, require_lowercase=False,
            require_number=False, require_symbol=False,
        )


def test_set_password_policy_blank_min_length_just_inherits_tenant(db_session, tenant_factory):
    tenant, _account, _users = tenant_factory.make(slug="cust-svc-password-policy-inherit")
    customer = customer_service.create_customer(
        db_session, tenant.id, customer_service.CustomerInput(customer_number="C-1", name="Acme")
    )
    db_session.commit()

    updated = customer_service.set_password_policy(
        db_session, tenant.id, customer.id, min_length=None, require_uppercase=True, require_lowercase=False,
        require_number=False, require_symbol=False,
    )
    db_session.commit()

    assert updated.password_min_length is None
    assert updated.password_require_uppercase is True


def test_set_password_policy_unknown_customer_returns_none(db_session, tenant_factory):
    import uuid

    tenant, _account, _users = tenant_factory.make(slug="cust-svc-password-policy-unknown")

    result = customer_service.set_password_policy(
        db_session, tenant.id, uuid.uuid4(), min_length=None, require_uppercase=False, require_lowercase=False,
        require_number=False, require_symbol=False,
    )
    assert result is None
