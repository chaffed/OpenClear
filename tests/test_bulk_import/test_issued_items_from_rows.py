# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

from openclear.repositories.account_repo import AccountRepository
from openclear.repositories.issued_item_repo import IssuedItemRepository
from openclear.services.issued_item_service import create_issued_items_from_rows


def test_creates_issued_items_from_valid_rows(db_session, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="bulk-issued-valid")
    rows = [
        {"account_number": account.account_number, "check_number": "5001", "amount": "150.00", "payee_name": "Vendor A", "issue_date": "2026-01-01"},
        {"account_number": account.account_number, "check_number": "5002", "amount": "99.99", "payee_name": "Vendor B", "issue_date": "2026-01-02"},
    ]

    results = create_issued_items_from_rows(db_session, tenant.id, rows, submitted_by_user_id=users["preparer"].id)

    assert all(r.success for r in results)
    items = IssuedItemRepository(db_session, tenant.id).list()
    assert len(items) == 2


def test_bad_row_does_not_roll_back_good_rows(db_session, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="bulk-issued-partial")
    rows = [
        {"account_number": account.account_number, "check_number": "6001", "amount": "150.00", "payee_name": "Vendor A", "issue_date": "2026-01-01"},
        {"account_number": account.account_number, "check_number": "6002", "amount": "not-a-number", "payee_name": "Vendor B", "issue_date": "2026-01-02"},
        {"account_number": "does-not-exist", "check_number": "6003", "amount": "50.00", "payee_name": "Vendor C", "issue_date": "2026-01-03"},
    ]

    results = create_issued_items_from_rows(db_session, tenant.id, rows, submitted_by_user_id=users["preparer"].id)

    assert results[0].success is True
    assert results[1].success is False
    assert "amount" in results[1].error
    assert results[2].success is False
    assert "No account found" in results[2].error

    items = IssuedItemRepository(db_session, tenant.id).list()
    assert len(items) == 1
    assert items[0].check_number == "6001"


def test_missing_required_field_reported_per_row(db_session, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="bulk-issued-missing-field")
    rows = [{"account_number": account.account_number, "check_number": "7001"}]  # missing amount/payee/date

    results = create_issued_items_from_rows(db_session, tenant.id, rows, submitted_by_user_id=users["preparer"].id)

    assert results[0].success is False
    assert "is required" in results[0].error


def test_auto_create_accounts_creates_missing_account(db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="bulk-issued-auto-create")
    rows = [
        {"account_number": "9999", "check_number": "8001", "amount": "10.00", "payee_name": "Vendor A", "issue_date": "2026-01-01"},
    ]

    results = create_issued_items_from_rows(
        db_session, tenant.id, rows, submitted_by_user_id=users["preparer"].id, auto_create_accounts=True
    )

    assert results[0].success is True
    accounts = AccountRepository(db_session, tenant.id).list(account_number="9999")
    assert len(accounts) == 1
    assert accounts[0].name == "Account 9999"


def test_auto_create_accounts_uses_account_name_column(db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="bulk-issued-auto-create-name")
    rows = [
        {
            "account_number": "8888",
            "account_name": "Payroll",
            "check_number": "8002",
            "amount": "10.00",
            "payee_name": "Vendor A",
            "issue_date": "2026-01-01",
        },
    ]

    create_issued_items_from_rows(db_session, tenant.id, rows, submitted_by_user_id=users["preparer"].id, auto_create_accounts=True)

    accounts = AccountRepository(db_session, tenant.id).list(account_number="8888")
    assert accounts[0].name == "Payroll"


def test_auto_create_accounts_reuses_same_new_account_across_rows(db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="bulk-issued-auto-create-dedup")
    rows = [
        {"account_number": "7777", "check_number": "8003", "amount": "10.00", "payee_name": "Vendor A", "issue_date": "2026-01-01"},
        {"account_number": "7777", "check_number": "8004", "amount": "20.00", "payee_name": "Vendor B", "issue_date": "2026-01-02"},
    ]

    results = create_issued_items_from_rows(
        db_session, tenant.id, rows, submitted_by_user_id=users["preparer"].id, auto_create_accounts=True
    )

    assert all(r.success for r in results)
    accounts = AccountRepository(db_session, tenant.id).list(account_number="7777")
    assert len(accounts) == 1
    items = IssuedItemRepository(db_session, tenant.id).list()
    assert {item.account_id for item in items} == {accounts[0].id}


def test_auto_create_accounts_off_still_reports_missing_account(db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="bulk-issued-auto-create-off")
    rows = [{"account_number": "6666", "check_number": "8005", "amount": "10.00", "payee_name": "Vendor A", "issue_date": "2026-01-01"}]

    results = create_issued_items_from_rows(db_session, tenant.id, rows, submitted_by_user_id=users["preparer"].id)

    assert results[0].success is False
    assert "No account found" in results[0].error
    assert AccountRepository(db_session, tenant.id).list(account_number="6666") == []
