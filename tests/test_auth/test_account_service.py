# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import pytest
from sqlalchemy.exc import IntegrityError

from openclear.services import account_service, customer_service
from openclear.services.account_service import AccountInput


def test_create_account_with_external_id(db_session, tenant_factory):
    tenant, _account, _users = tenant_factory.make(slug="acct-create-external-id")

    account = account_service.create_account(
        db_session, tenant.id, AccountInput(account_number="1001", name="Operating", external_account_id="CUST-REF-1")
    )
    db_session.commit()

    assert account.external_account_id == "CUST-REF-1"


def test_get_account_by_number_resolves_by_real_number(db_session, tenant_factory):
    tenant, _account, _users = tenant_factory.make(slug="acct-resolve-number")
    account_service.create_account(db_session, tenant.id, AccountInput(account_number="1001", name="Operating"))
    db_session.commit()

    found = account_service.get_account_by_number(db_session, tenant.id, "1001")

    assert found is not None
    assert found.account_number == "1001"


def test_get_account_by_number_falls_back_to_external_id(db_session, tenant_factory):
    tenant, _account, _users = tenant_factory.make(slug="acct-fallback-external")
    account_service.create_account(
        db_session, tenant.id, AccountInput(account_number="1001", name="Operating", external_account_id="CUST-REF-1")
    )
    db_session.commit()

    found = account_service.get_account_by_number(db_session, tenant.id, "CUST-REF-1")

    assert found is not None
    assert found.account_number == "1001"


def test_get_account_by_number_prefers_real_number_over_external_id_collision(db_session, tenant_factory):
    """If one account's external_account_id happens to equal a DIFFERENT account's real
    account_number, the real number always wins — resolution tries account_number
    across every account before ever falling back to external_account_id."""
    tenant, _account, _users = tenant_factory.make(slug="acct-collision")
    account_a = account_service.create_account(db_session, tenant.id, AccountInput(account_number="1001", name="A"))
    account_service.create_account(
        db_session, tenant.id, AccountInput(account_number="2002", name="B", external_account_id="1001")
    )
    db_session.commit()

    found = account_service.get_account_by_number(db_session, tenant.id, "1001")

    assert found.id == account_a.id


def test_get_account_by_number_returns_none_when_nothing_matches(db_session, tenant_factory):
    tenant, _account, _users = tenant_factory.make(slug="acct-no-match")

    assert account_service.get_account_by_number(db_session, tenant.id, "nonexistent") is None


def test_get_account_by_number_customer_scoping_applies_to_external_id_too(db_session, tenant_factory):
    tenant, _house_account, _users = tenant_factory.make(slug="acct-scoped-external")
    customer_a = customer_service.create_customer(db_session, tenant.id, customer_service.CustomerInput(customer_number="C-A", name="A"))
    customer_b = customer_service.create_customer(db_session, tenant.id, customer_service.CustomerInput(customer_number="C-B", name="B"))
    account_service.create_account(
        db_session, tenant.id, AccountInput(account_number="A-1", name="A Acct", customer_id=customer_a.id, external_account_id="ref-1")
    )
    db_session.commit()

    assert account_service.get_account_by_number(db_session, tenant.id, "ref-1", customer_id=customer_a.id) is not None
    assert account_service.get_account_by_number(db_session, tenant.id, "ref-1", customer_id=customer_b.id) is None


def test_external_account_id_unique_per_tenant(db_session, tenant_factory):
    tenant, _account, _users = tenant_factory.make(slug="acct-unique-external")
    account_service.create_account(
        db_session, tenant.id, AccountInput(account_number="1001", name="A", external_account_id="dupe-ref")
    )
    db_session.commit()

    with pytest.raises(IntegrityError):
        account_service.create_account(
            db_session, tenant.id, AccountInput(account_number="2002", name="B", external_account_id="dupe-ref")
        )
    db_session.rollback()


def test_get_or_create_account_by_number_auto_create_uses_raw_value_as_account_number_only(db_session, tenant_factory):
    tenant, _account, _users = tenant_factory.make(slug="acct-autocreate")

    account = account_service.get_or_create_account_by_number(db_session, tenant.id, "new-ref-999", default_name="Auto")
    db_session.commit()

    assert account.account_number == "new-ref-999"
    assert account.external_account_id is None


def test_bulk_account_upload_sets_external_account_id(db_session, tenant_factory):
    tenant, _account, _users = tenant_factory.make(slug="acct-bulk-external")

    rows = [{"account_number": "1001", "name": "Operating", "external_account_id": "CUST-REF-9"}]
    results = account_service.create_accounts_from_rows(db_session, tenant.id, rows)

    assert results[0].success is True
    account = account_service.get_account_by_number(db_session, tenant.id, "CUST-REF-9")
    assert account is not None
    assert account.account_number == "1001"
