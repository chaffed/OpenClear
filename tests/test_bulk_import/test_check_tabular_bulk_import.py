# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

from openclear.networks.check.bulk_import import ingest_paid_item_tabular_rows
from openclear.repositories.paid_item_repo import PaidItemRepository


def test_creates_paid_items_from_rows(db_session, tenant_factory):
    tenant, account, _users = tenant_factory.make(slug="bulk-check-tabular-rows")
    rows = [
        {
            "account_number": account.account_number,
            "check_number": "5001",
            "presented_amount": "150.00",
            "presented_date": "2026-01-15",
        },
        {
            "account_number": account.account_number,
            "check_number": "5002",
            "presented_amount": "not-a-number",
            "presented_date": "2026-01-15",
        },
    ]

    results = ingest_paid_item_tabular_rows(db_session, tenant.id, rows)

    assert results[0].success is True
    assert results[1].success is False

    items = PaidItemRepository(db_session, tenant.id).list()
    assert len(items) == 1
    assert items[0].check_number == "5001"


def test_bad_account_number_isolated(db_session, tenant_factory):
    tenant, _account, _users = tenant_factory.make(slug="bulk-check-tabular-bad-account")
    rows = [
        {"account_number": "nonexistent", "check_number": "5001", "presented_amount": "10.00", "presented_date": "2026-01-15"}
    ]
    results = ingest_paid_item_tabular_rows(db_session, tenant.id, rows)
    assert results[0].success is False
    assert "No account found" in results[0].error


def test_auto_create_accounts_creates_missing_account(db_session, tenant_factory):
    from openclear.services import account_service

    tenant, _account, _users = tenant_factory.make(slug="bulk-check-tabular-auto-create")
    rows = [
        {
            "account_number": "9999",
            "account_name": "New Client Account",
            "check_number": "5001",
            "presented_amount": "10.00",
            "presented_date": "2026-01-15",
        }
    ]
    results = ingest_paid_item_tabular_rows(db_session, tenant.id, rows, auto_create_accounts=True)
    assert results[0].success is True
    account = account_service.get_account_by_number(db_session, tenant.id, "9999")
    assert account is not None
    assert account.name == "New Client Account"
