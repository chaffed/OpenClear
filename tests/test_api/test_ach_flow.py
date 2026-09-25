# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

from tests.conftest import login_headers


def test_authorized_debit_within_limits_matches(client, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="ach-match")
    headers = login_headers(client, tenant.slug, users["preparer"].email)

    client.post(
        "/api/v1/ach/authorizations",
        headers=headers,
        json={
            "account_id": str(account.id),
            "originator_id": "ORIG001",
            "originator_name": "Utility Co",
            "max_amount": "500.00",
            "effective_date": "2026-01-01",
        },
    )

    txn = client.post(
        "/api/v1/ach/transactions",
        headers=headers,
        json={
            "account_id": str(account.id),
            "originator_id": "ORIG001",
            "originator_name": "Utility Co",
            "amount": "150.00",
            "transaction_type": "debit",
            "sec_code": "PPD",
            "trace_number": "TRACE0001",
            "effective_date": "2026-01-10",
        },
    )
    assert txn.status_code == 201, txn.text
    assert txn.json()["match_status"] == "matched"
    assert txn.json()["settlement_status"] == "paid"

    exceptions = client.get("/api/v1/exceptions", headers=headers).json()
    assert exceptions == []


def test_unauthorized_originator_creates_exception_through_shared_endpoints(client, db_session, tenant_factory):
    """Proves the network-adapter abstraction holds: /exceptions and /decide are the exact
    same endpoints the check flow uses, unmodified, now serving an ACH exception."""
    from openclear.services import ach_return_reason_service

    tenant, account, users = tenant_factory.make(slug="ach-unauthorized")
    headers = login_headers(client, tenant.slug, users["preparer"].email)

    txn = client.post(
        "/api/v1/ach/transactions",
        headers=headers,
        json={
            "account_id": str(account.id),
            "originator_id": "UNKNOWN01",
            "originator_name": "Suspicious LLC",
            "amount": "75.00",
            "transaction_type": "debit",
            "sec_code": "WEB",
            "trace_number": "TRACE0002",
            "effective_date": "2026-01-10",
        },
    )
    assert txn.json()["match_status"] == "exception"

    exceptions = client.get("/api/v1/exceptions", headers=headers).json()
    assert len(exceptions) == 1
    assert exceptions[0]["network_code"] == "ach"
    assert exceptions[0]["exception_types"] == ["unauthorized_originator"]
    exception_id = exceptions[0]["id"]

    return_reason = ach_return_reason_service.list_ach_return_reasons(db_session, tenant.id, active_only=True)[0]
    approver_headers = login_headers(client, tenant.slug, users["approver"].email)
    decide = client.post(
        f"/api/v1/exceptions/{exception_id}/decide",
        headers=approver_headers,
        json={"outcome": "return", "ach_return_reason_id": str(return_reason.id)},
    )
    assert decide.status_code == 200
    assert decide.json()["outcome"] == "return"
    assert decide.json()["reason_code"] == return_reason.reason_text


def test_amount_exceeds_authorized_limit(client, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="ach-over-limit")
    headers = login_headers(client, tenant.slug, users["preparer"].email)

    client.post(
        "/api/v1/ach/authorizations",
        headers=headers,
        json={
            "account_id": str(account.id),
            "originator_id": "ORIG002",
            "originator_name": "Insurance Co",
            "max_amount": "200.00",
            "effective_date": "2026-01-01",
        },
    )
    txn = client.post(
        "/api/v1/ach/transactions",
        headers=headers,
        json={
            "account_id": str(account.id),
            "originator_id": "ORIG002",
            "originator_name": "Insurance Co",
            "amount": "999.00",
            "transaction_type": "debit",
            "sec_code": "PPD",
            "trace_number": "TRACE0003",
            "effective_date": "2026-01-10",
        },
    )
    assert txn.json()["match_status"] == "exception"

    exceptions = client.get("/api/v1/exceptions", headers=headers).json()
    assert "amount_exceeds_limit" in exceptions[0]["exception_types"]


def test_debit_block_all_rejects_even_authorized_originator(db_session, tenant_factory):
    """No admin API exposes ach_debit_block_mode yet (Phase 6+), so this exercises the
    ingestion service directly against an account flipped into block_all mode."""
    from datetime import date
    from decimal import Decimal

    from openclear.domain.account import AchDebitBlockMode
    from openclear.domain.ach_transaction import AchTransactionType
    from openclear.networks.ach.ingestion import AchTransactionSubmission, ingest_ach_transaction
    from openclear.services import ach_authorization_service

    tenant, account, users = tenant_factory.make(slug="ach-block-all")
    account.ach_debit_block_mode = AchDebitBlockMode.BLOCK_ALL
    db_session.flush()

    ach_authorization_service.create_ach_authorization(
        db_session,
        tenant.id,
        ach_authorization_service.AchAuthorizationInput(
            account_id=account.id,
            originator_id="ORIG003",
            originator_name="Payroll Co",
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

    txn = ingest_ach_transaction(
        db_session,
        tenant.id,
        AchTransactionSubmission(
            account_id=account.id,
            originator_id="ORIG003",
            originator_name="Payroll Co",
            amount=Decimal("50.00"),
            transaction_type=AchTransactionType.DEBIT,
            sec_code="PPD",
            trace_number="TRACE0099",
            effective_date=date(2026, 1, 10),
        ),
    )
    db_session.commit()

    assert txn.match_status.value == "exception"


def test_revoke_ach_authorization(client, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="ach-revoke")
    headers = login_headers(client, tenant.slug, users["preparer"].email)

    created = client.post(
        "/api/v1/ach/authorizations",
        headers=headers,
        json={
            "account_id": str(account.id),
            "originator_id": "ORIG004",
            "originator_name": "Gym Membership Co",
            "effective_date": "2026-01-01",
        },
    ).json()

    revoked = client.patch(f"/api/v1/ach/authorizations/{created['id']}/revoke", headers=headers)
    assert revoked.status_code == 200
    assert revoked.json()["status"] == "revoked"

    # A debit against the now-revoked authorization must be treated as unauthorized.
    txn = client.post(
        "/api/v1/ach/transactions",
        headers=headers,
        json={
            "account_id": str(account.id),
            "originator_id": "ORIG004",
            "originator_name": "Gym Membership Co",
            "amount": "50.00",
            "transaction_type": "debit",
            "sec_code": "PPD",
            "trace_number": "TRACE0004",
            "effective_date": "2026-01-10",
        },
    )
    assert txn.json()["match_status"] == "exception"


def test_credit_transactions_never_flagged(client, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="ach-credit")
    headers = login_headers(client, tenant.slug, users["preparer"].email)

    txn = client.post(
        "/api/v1/ach/transactions",
        headers=headers,
        json={
            "account_id": str(account.id),
            "originator_id": "ANYONE",
            "originator_name": "Refund Co",
            "amount": "50.00",
            "transaction_type": "credit",
            "sec_code": "PPD",
            "trace_number": "TRACE0005",
            "effective_date": "2026-01-10",
        },
    )
    assert txn.json()["match_status"] == "matched"


def test_receiver_scoped_authorization_rejects_other_receivers(client, tenant_factory):
    """An authorization scoped to one receiver_id (e.g. one employee's payroll ID) must
    not authorize debits for a different receiver_id under the same originator."""
    tenant, account, users = tenant_factory.make(slug="ach-receiver-scoped")
    headers = login_headers(client, tenant.slug, users["preparer"].email)

    client.post(
        "/api/v1/ach/authorizations",
        headers=headers,
        json={
            "account_id": str(account.id),
            "originator_id": "PAYROLL01",
            "originator_name": "Acme Payroll",
            "receiver_id": "EMP-001",
            "max_amount": "2000.00",
            "effective_date": "2026-01-01",
        },
    )

    matching = client.post(
        "/api/v1/ach/transactions",
        headers=headers,
        json={
            "account_id": str(account.id),
            "originator_id": "PAYROLL01",
            "originator_name": "Acme Payroll",
            "receiver_id": "EMP-001",
            "amount": "1500.00",
            "transaction_type": "debit",
            "sec_code": "PPD",
            "trace_number": "TRACE0006",
            "effective_date": "2026-01-10",
        },
    )
    assert matching.json()["match_status"] == "matched"

    other_receiver = client.post(
        "/api/v1/ach/transactions",
        headers=headers,
        json={
            "account_id": str(account.id),
            "originator_id": "PAYROLL01",
            "originator_name": "Acme Payroll",
            "receiver_id": "EMP-999",
            "amount": "1500.00",
            "transaction_type": "debit",
            "sec_code": "PPD",
            "trace_number": "TRACE0007",
            "effective_date": "2026-01-10",
        },
    )
    assert other_receiver.json()["match_status"] == "exception"

    exceptions = client.get("/api/v1/exceptions", headers=headers).json()
    matching_exception = next(e for e in exceptions if e["source_item_id"] == other_receiver.json()["id"])
    assert matching_exception["exception_types"] == ["receiver_id_not_permitted"]


def test_wildcard_authorization_covers_any_receiver(client, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="ach-wildcard-receiver")
    headers = login_headers(client, tenant.slug, users["preparer"].email)

    client.post(
        "/api/v1/ach/authorizations",
        headers=headers,
        json={
            "account_id": str(account.id),
            "originator_id": "UTIL01",
            "originator_name": "City Utility",
            "effective_date": "2026-01-01",
        },
    )

    for i, receiver in enumerate(["ACCT-1", "ACCT-2", "ACCT-3"]):
        txn = client.post(
            "/api/v1/ach/transactions",
            headers=headers,
            json={
                "account_id": str(account.id),
                "originator_id": "UTIL01",
                "originator_name": "City Utility",
                "receiver_id": receiver,
                "amount": "40.00",
                "transaction_type": "debit",
                "sec_code": "PPD",
                "trace_number": f"TRACE010{i}",
                "effective_date": "2026-01-10",
            },
        )
        assert txn.json()["match_status"] == "matched"


def test_exact_receiver_rule_takes_precedence_over_wildcard(client, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="ach-precedence")
    headers = login_headers(client, tenant.slug, users["preparer"].email)

    client.post(
        "/api/v1/ach/authorizations",
        headers=headers,
        json={
            "account_id": str(account.id),
            "originator_id": "VENDOR01",
            "originator_name": "Vendor Co",
            "max_amount": "1000.00",
            "effective_date": "2026-01-01",
        },
    )
    client.post(
        "/api/v1/ach/authorizations",
        headers=headers,
        json={
            "account_id": str(account.id),
            "originator_id": "VENDOR01",
            "originator_name": "Vendor Co",
            "receiver_id": "TIGHT-001",
            "max_amount": "50.00",
            "effective_date": "2026-01-01",
        },
    )

    # Under the blanket rule's $1000 limit, but over the receiver-specific rule's $50 limit.
    txn = client.post(
        "/api/v1/ach/transactions",
        headers=headers,
        json={
            "account_id": str(account.id),
            "originator_id": "VENDOR01",
            "originator_name": "Vendor Co",
            "receiver_id": "TIGHT-001",
            "amount": "200.00",
            "transaction_type": "debit",
            "sec_code": "PPD",
            "trace_number": "TRACE0200",
            "effective_date": "2026-01-10",
        },
    )
    assert txn.json()["match_status"] == "exception"
    exceptions = client.get("/api/v1/exceptions", headers=headers).json()
    matching_exception = next(e for e in exceptions if e["source_item_id"] == txn.json()["id"])
    assert matching_exception["exception_types"] == ["amount_exceeds_limit"]
