# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""exception_item.source_item_id has no DB-level foreign key (see docs/ARCHITECTURE.md, "Data model" —
a deliberate trade for N-network extensibility). The mitigation is: every exception_item
is created in the same transaction as its source row, and resolution only ever happens
through the owning network's adapter.load_source_item(). This suite verifies that
mitigation actually holds for every exception created through the real ingestion paths."""

from datetime import date
from decimal import Decimal

from openclear.networks.registry import get_adapter
from openclear.repositories.exception_repo import ExceptionRepository
from tests.conftest import login_headers


def test_check_exception_source_pointer_resolves_to_a_real_paid_item(client, db_session, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="ptr-check")
    headers = login_headers(client, tenant.slug, users["preparer"].email)

    client.post(
        "/api/v1/paid-items",
        headers=headers,
        json={
            "account_id": str(account.id),
            "check_number": "1",
            "presented_amount": "10.00",
            "presented_date": "2026-01-01",
        },
    )

    exceptions = ExceptionRepository(db_session, tenant.id).list(network_code="check")
    assert len(exceptions) == 1

    adapter = get_adapter("check")
    source = adapter.load_source_item(db_session, exceptions[0].source_item_id)

    assert source is not None
    assert source.id == exceptions[0].source_item_id
    assert source.tenant_id == tenant.id


def test_ach_exception_source_pointer_resolves_to_a_real_transaction(client, db_session, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="ptr-ach")
    headers = login_headers(client, tenant.slug, users["preparer"].email)

    client.post(
        "/api/v1/ach/transactions",
        headers=headers,
        json={
            "account_id": str(account.id),
            "originator_id": "UNKNOWN",
            "originator_name": "X",
            "amount": "10.00",
            "transaction_type": "debit",
            "sec_code": "PPD",
            "trace_number": "T1",
            "effective_date": "2026-01-01",
        },
    )

    exceptions = ExceptionRepository(db_session, tenant.id).list(network_code="ach")
    assert len(exceptions) == 1

    adapter = get_adapter("ach")
    source = adapter.load_source_item(db_session, exceptions[0].source_item_id)

    assert source is not None
    assert source.id == exceptions[0].source_item_id
    assert source.tenant_id == tenant.id


def test_every_exception_in_the_database_has_a_resolvable_source(client, db_session, tenant_factory):
    """A broader sweep: generate a mix of check and ACH exceptions, then confirm every
    single exception_item row in the DB resolves via its network's adapter — the
    end-to-end version of the two targeted tests above."""
    tenant, account, users = tenant_factory.make(slug="ptr-sweep")
    headers = login_headers(client, tenant.slug, users["preparer"].email)

    for i in range(3):
        client.post(
            "/api/v1/paid-items",
            headers=headers,
            json={
                "account_id": str(account.id),
                "check_number": f"c{i}",
                "presented_amount": "10.00",
                "presented_date": "2026-01-01",
            },
        )
        client.post(
            "/api/v1/ach/transactions",
            headers=headers,
            json={
                "account_id": str(account.id),
                "originator_id": f"ORIG{i}",
                "originator_name": "X",
                "amount": "10.00",
                "transaction_type": "debit",
                "sec_code": "PPD",
                "trace_number": f"T{i}",
                "effective_date": "2026-01-01",
            },
        )

    all_exceptions = ExceptionRepository(db_session, tenant.id).list()
    assert len(all_exceptions) == 6

    for exc in all_exceptions:
        adapter = get_adapter(exc.network_code)
        source = adapter.load_source_item(db_session, exc.source_item_id)
        assert source is not None, f"orphaned pointer: exception {exc.id} -> {exc.network_code}:{exc.source_item_id}"
