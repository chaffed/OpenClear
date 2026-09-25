# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

from openclear.networks.ach.bulk_import import ingest_ach_rows, ingest_nacha_entries
from openclear.repositories.account_repo import AccountRepository
from openclear.repositories.ach_transaction_repo import AchTransactionRepository
from openclear.services import ach_authorization_service
from datetime import date


def test_creates_ach_transactions_from_rows(db_session, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="bulk-ach-rows")
    rows = [
        {
            "account_number": account.account_number,
            "originator_id": "ORIG1",
            "originator_name": "Payroll Co",
            "receiver_id": "",
            "amount": "10.00",
            "transaction_type": "credit",
            "sec_code": "PPD",
            "trace_number": "T1",
            "effective_date": "2026-01-10",
        },
        {
            "account_number": account.account_number,
            "originator_id": "ORIG2",
            "originator_name": "Unknown Co",
            "receiver_id": "",
            "amount": "999.00",
            "transaction_type": "debit",
            "sec_code": "WEB",
            "trace_number": "T2",
            "effective_date": "2026-01-10",
        },
    ]

    results = ingest_ach_rows(db_session, tenant.id, rows)

    assert all(r.success for r in results)
    txns = AchTransactionRepository(db_session, tenant.id).list()
    assert len(txns) == 2
    # unauthorized debit correctly becomes an exception, not an ingestion failure
    debit_txn = next(t for t in txns if t.originator_id == "ORIG2")
    assert debit_txn.match_status.value == "exception"
    credit_txn = next(t for t in txns if t.originator_id == "ORIG1")
    assert credit_txn.match_status.value == "matched"


def test_ach_rows_bad_account_isolated(db_session, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="bulk-ach-bad-account")
    rows = [
        {
            "account_number": "nonexistent",
            "originator_id": "ORIG1",
            "originator_name": "Payroll Co",
            "amount": "10.00",
            "transaction_type": "credit",
            "sec_code": "PPD",
            "trace_number": "T1",
            "effective_date": "2026-01-10",
        }
    ]
    results = ingest_ach_rows(db_session, tenant.id, rows)
    assert results[0].success is False
    assert "No account found" in results[0].error


def test_ingest_nacha_entries_creates_transactions(db_session, tenant_factory):
    from openclear.bulk_import.nacha import NachaEntry

    tenant, account, users = tenant_factory.make(slug="bulk-ach-nacha")
    ach_authorization_service.create_ach_authorization(
        db_session,
        tenant.id,
        ach_authorization_service.AchAuthorizationInput(
            account_id=account.id,
            originator_id="1234567890",
            originator_name="ACME CORP",
            receiver_id=None,
            max_amount=None,
            frequency_limit=None,
            allowed_sec_codes=None,
            effective_date=date(2026, 1, 1),
            expiration_date=None,
        ),
        created_by_user_id=users["preparer"].id,
    )
    db_session.commit()

    entry = NachaEntry(
        line_number=2,
        dfi_account_number=account.account_number,
        amount=150,
        individual_id="EMP001",
        transaction_code="22",
        trace_number="123456780000001",
        company_id="1234567890",
        company_name="ACME CORP",
        sec_code="PPD",
        effective_date=date(2026, 1, 10),
    )

    results = ingest_nacha_entries(db_session, tenant.id, [entry])

    assert results[0].success is True
    txns = AchTransactionRepository(db_session, tenant.id).list()
    assert len(txns) == 1
    assert txns[0].match_status.value == "matched"
    assert txns[0].transaction_type.value == "credit"


def test_ingest_nacha_entries_unmatched_account_reported(db_session, tenant_factory):
    from openclear.bulk_import.nacha import NachaEntry

    tenant, account, users = tenant_factory.make(slug="bulk-ach-nacha-bad-acct")
    entry = NachaEntry(
        line_number=2,
        dfi_account_number="0000000000",
        amount=150,
        individual_id=None,
        transaction_code="27",
        trace_number="123456780000002",
        company_id="9999999999",
        company_name="SOME CO",
        sec_code="CCD",
        effective_date=date(2026, 1, 10),
    )

    results = ingest_nacha_entries(db_session, tenant.id, [entry])

    assert results[0].success is False
    assert "No account found" in results[0].error
    assert "trace 123456780000002" in results[0].row_label


def test_ingest_ach_rows_auto_create_accounts(db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="bulk-ach-auto-create")
    rows = [
        {
            "account_number": "5555",
            "originator_id": "ORIG1",
            "originator_name": "Payroll Co",
            "amount": "10.00",
            "transaction_type": "credit",
            "sec_code": "PPD",
            "trace_number": "T1",
            "effective_date": "2026-01-10",
        }
    ]

    results = ingest_ach_rows(db_session, tenant.id, rows, auto_create_accounts=True)

    assert results[0].success is True
    accounts = AccountRepository(db_session, tenant.id).list(account_number="5555")
    assert len(accounts) == 1
    assert accounts[0].name == "Account 5555"


def test_ingest_nacha_entries_auto_create_accounts(db_session, tenant_factory):
    from openclear.bulk_import.nacha import NachaEntry

    tenant, _account, users = tenant_factory.make(slug="bulk-ach-nacha-auto-create")
    entry = NachaEntry(
        line_number=2,
        dfi_account_number="4444",
        amount=150,
        individual_id=None,
        transaction_code="27",
        trace_number="123456780000003",
        company_id="9999999999",
        company_name="SOME CO",
        sec_code="CCD",
        effective_date=date(2026, 1, 10),
    )

    results = ingest_nacha_entries(db_session, tenant.id, [entry], auto_create_accounts=True)

    assert results[0].success is True
    accounts = AccountRepository(db_session, tenant.id).list(account_number="4444")
    assert len(accounts) == 1
    assert accounts[0].name == "Account 4444"
