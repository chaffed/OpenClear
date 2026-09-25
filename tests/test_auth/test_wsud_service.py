# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import uuid
from datetime import date
from decimal import Decimal

import pytest

from openclear.domain.ach_transaction import (
    AchMatchStatus,
    AchSettlementStatus,
    AchTransaction,
    AchTransactionSource,
    AchTransactionType,
)
from openclear.services import account_service, customer_service, wsud_service


def _make_customer_and_account(db_session, tenant, number="C1"):
    customer = customer_service.create_customer(
        db_session, tenant.id, customer_service.CustomerInput(customer_number=number, name=f"Customer {number}")
    )
    account = account_service.create_account(
        db_session, tenant.id, account_service.AccountInput(account_number=f"A-{number}", name="Op", customer_id=customer.id)
    )
    db_session.flush()
    return customer, account


def _make_ach_txn(db_session, tenant, customer, account, *, sec_code="PPD", settlement_status=AchSettlementStatus.RETURNED, trace="T1"):
    txn = AchTransaction(
        tenant_id=tenant.id, account_id=account.id, customer_id=customer.id,
        originator_id="ORIG", originator_name="Orig Co", amount=Decimal("50.00"),
        transaction_type=AchTransactionType.DEBIT, sec_code=sec_code, trace_number=trace,
        effective_date=date(2026, 1, 1), match_status=AchMatchStatus.EXCEPTION,
        settlement_status=settlement_status, source=AchTransactionSource.API,
    )
    db_session.add(txn)
    db_session.flush()
    return txn


def test_eligible_transactions_filtered_by_consumer_sec_code(db_session, tenant_factory):
    tenant, _account, _users = tenant_factory.make(slug="wsud-sec-code-filter")
    customer, account = _make_customer_and_account(db_session, tenant)
    consumer_txn = _make_ach_txn(db_session, tenant, customer, account, sec_code="PPD", trace="T1")
    _make_ach_txn(db_session, tenant, customer, account, sec_code="CCD", trace="T2")

    eligible = wsud_service.list_wsud_eligible_transactions(db_session, tenant.id, customer.id)

    assert [e.transaction.id for e in eligible] == [consumer_txn.id]


def test_eligible_transactions_filtered_by_returned_status(db_session, tenant_factory):
    tenant, _account, _users = tenant_factory.make(slug="wsud-status-filter")
    customer, account = _make_customer_and_account(db_session, tenant)
    returned_txn = _make_ach_txn(db_session, tenant, customer, account, settlement_status=AchSettlementStatus.RETURNED, trace="T1")
    _make_ach_txn(db_session, tenant, customer, account, settlement_status=AchSettlementStatus.PAID, trace="T2")
    _make_ach_txn(db_session, tenant, customer, account, settlement_status=AchSettlementStatus.PENDING, trace="T3")

    eligible = wsud_service.list_wsud_eligible_transactions(db_session, tenant.id, customer.id)

    assert [e.transaction.id for e in eligible] == [returned_txn.id]


def test_eligible_transactions_scoped_to_customer(db_session, tenant_factory):
    tenant, _account, _users = tenant_factory.make(slug="wsud-customer-scope")
    customer_a, account_a = _make_customer_and_account(db_session, tenant, number="A")
    customer_b, account_b = _make_customer_and_account(db_session, tenant, number="B")
    _make_ach_txn(db_session, tenant, customer_a, account_a, trace="TA")
    _make_ach_txn(db_session, tenant, customer_b, account_b, trace="TB")

    eligible_a = wsud_service.list_wsud_eligible_transactions(db_session, tenant.id, customer_a.id)

    assert len(eligible_a) == 1
    assert eligible_a[0].transaction.customer_id == customer_a.id


def test_sign_wsud_statement_creates_signed_record(db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="wsud-sign-basic")
    customer, account = _make_customer_and_account(db_session, tenant)
    txn = _make_ach_txn(db_session, tenant, customer, account)

    statement = wsud_service.sign_wsud_statement(
        db_session, tenant.id, customer.id,
        signed_by_user_id=users["admin"].id, signer_typed_name="Jane Doe",
        ach_transaction_ids=[txn.id], signer_ip_address="127.0.0.1", signer_user_agent="pytest",
    )
    db_session.commit()

    assert statement.customer_id == customer.id
    assert statement.signer_typed_name == "Jane Doe"
    assert statement.signature_hex
    covered = wsud_service.get_transactions_for_statement(db_session, statement)
    assert [t.id for t in covered] == [txn.id]


def test_sign_wsud_statement_rejects_ineligible_transaction(db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="wsud-sign-ineligible")
    customer, account = _make_customer_and_account(db_session, tenant)
    _make_ach_txn(db_session, tenant, customer, account, settlement_status=AchSettlementStatus.PAID)
    ineligible_txn = db_session.query(AchTransaction).filter_by(tenant_id=tenant.id).first()

    with pytest.raises(ValueError):
        wsud_service.sign_wsud_statement(
            db_session, tenant.id, customer.id,
            signed_by_user_id=users["admin"].id, signer_typed_name="Jane Doe",
            ach_transaction_ids=[ineligible_txn.id], signer_ip_address=None, signer_user_agent=None,
        )


def test_sign_wsud_statement_rejects_unrelated_transaction_id(db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="wsud-sign-unrelated")
    customer, account = _make_customer_and_account(db_session, tenant)
    _make_ach_txn(db_session, tenant, customer, account)

    with pytest.raises(ValueError):
        wsud_service.sign_wsud_statement(
            db_session, tenant.id, customer.id,
            signed_by_user_id=users["admin"].id, signer_typed_name="Jane Doe",
            ach_transaction_ids=[uuid.uuid4()], signer_ip_address=None, signer_user_agent=None,
        )


def test_sign_wsud_statement_rejects_empty_selection(db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="wsud-sign-empty")
    customer, _account_row = _make_customer_and_account(db_session, tenant)

    with pytest.raises(ValueError):
        wsud_service.sign_wsud_statement(
            db_session, tenant.id, customer.id,
            signed_by_user_id=users["admin"].id, signer_typed_name="Jane Doe",
            ach_transaction_ids=[], signer_ip_address=None, signer_user_agent=None,
        )


def test_sign_wsud_statement_rejects_blank_signer_name(db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="wsud-sign-blank-name")
    customer, account = _make_customer_and_account(db_session, tenant)
    txn = _make_ach_txn(db_session, tenant, customer, account)

    with pytest.raises(ValueError):
        wsud_service.sign_wsud_statement(
            db_session, tenant.id, customer.id,
            signed_by_user_id=users["admin"].id, signer_typed_name="   ",
            ach_transaction_ids=[txn.id], signer_ip_address=None, signer_user_agent=None,
        )


def test_verify_wsud_signature_valid_for_untampered_statement(db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="wsud-verify-valid")
    customer, account = _make_customer_and_account(db_session, tenant)
    txn = _make_ach_txn(db_session, tenant, customer, account)
    statement = wsud_service.sign_wsud_statement(
        db_session, tenant.id, customer.id,
        signed_by_user_id=users["admin"].id, signer_typed_name="Jane Doe",
        ach_transaction_ids=[txn.id], signer_ip_address=None, signer_user_agent=None,
    )
    db_session.commit()

    assert wsud_service.verify_wsud_signature(db_session, statement) is True


def test_verify_wsud_signature_detects_tampering(db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="wsud-verify-tamper")
    customer, account = _make_customer_and_account(db_session, tenant)
    txn = _make_ach_txn(db_session, tenant, customer, account)
    statement = wsud_service.sign_wsud_statement(
        db_session, tenant.id, customer.id,
        signed_by_user_id=users["admin"].id, signer_typed_name="Jane Doe",
        ach_transaction_ids=[txn.id], signer_ip_address=None, signer_user_agent=None,
    )
    db_session.commit()

    statement.signer_typed_name = "Someone Else"
    assert wsud_service.verify_wsud_signature(db_session, statement) is False


def test_already_signed_transaction_shown_as_already_covered_not_hidden(db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="wsud-already-covered")
    customer, account = _make_customer_and_account(db_session, tenant)
    txn = _make_ach_txn(db_session, tenant, customer, account)
    wsud_service.sign_wsud_statement(
        db_session, tenant.id, customer.id,
        signed_by_user_id=users["admin"].id, signer_typed_name="Jane Doe",
        ach_transaction_ids=[txn.id], signer_ip_address=None, signer_user_agent=None,
    )
    db_session.commit()

    eligible = wsud_service.list_wsud_eligible_transactions(db_session, tenant.id, customer.id)

    assert len(eligible) == 1
    assert eligible[0].already_covered is True


def test_list_wsud_statements_bank_wide_vs_customer_scoped(db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="wsud-list-scope")
    customer_a, account_a = _make_customer_and_account(db_session, tenant, number="A")
    customer_b, account_b = _make_customer_and_account(db_session, tenant, number="B")
    txn_a = _make_ach_txn(db_session, tenant, customer_a, account_a, trace="TA")
    txn_b = _make_ach_txn(db_session, tenant, customer_b, account_b, trace="TB")
    wsud_service.sign_wsud_statement(
        db_session, tenant.id, customer_a.id, signed_by_user_id=users["admin"].id, signer_typed_name="A Signer",
        ach_transaction_ids=[txn_a.id], signer_ip_address=None, signer_user_agent=None,
    )
    wsud_service.sign_wsud_statement(
        db_session, tenant.id, customer_b.id, signed_by_user_id=users["admin"].id, signer_typed_name="B Signer",
        ach_transaction_ids=[txn_b.id], signer_ip_address=None, signer_user_agent=None,
    )
    db_session.commit()

    all_statements = wsud_service.list_wsud_statements(db_session, tenant.id)
    scoped_to_a = wsud_service.list_wsud_statements(db_session, tenant.id, customer_a.id)

    assert len(all_statements) == 2
    assert len(scoped_to_a) == 1
    assert scoped_to_a[0].customer_id == customer_a.id
