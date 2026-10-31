# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""A check's settlement follows its decision (REVIEW_1_33_PLAN.md RV-D1): once a decision is
final, a paid check is paid (and its issued item with it) and a returned one returned, so the
same check presented again is a duplicate, on a reverse positive pay account too, where silence
pays. Before the cut-off nothing is settled, since the decision can still change."""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select

from openclear.domain.decision import DecisionOutcome
from openclear.domain.exception_item import ExceptionItem
from openclear.domain.issued_item import IssuedItemStatus
from openclear.domain.paid_item import PaidItem, PaidItemSettlementStatus, PaidItemSource
from openclear.networks.check.ingestion import PaidItemSubmission, ingest_paid_item
from openclear.services import decision_service, issued_item_service
from tests.test_decisions.test_changeable import _decide
from tests.test_networks.check.test_reverse_positive_pay import _present, _reverse_customer


def _lock(db_session, result, item):
    decision_service.lock(db_session, result.decision, item)
    db_session.commit()


def _paid(db_session, item) -> PaidItem:
    db_session.expire_all()
    return db_session.get(PaidItem, item.source_item_id)


def _present_positive(db_session, tenant, account, number, amount):
    paid = ingest_paid_item(db_session, tenant.id, PaidItemSubmission(
        account_id=account.id, check_number=number, presented_amount=Decimal(amount), presented_date=date(2026, 10, 21),
        source=PaidItemSource.API))
    item = db_session.execute(select(ExceptionItem).where(ExceptionItem.source_item_id == paid.id)).scalar_one()
    item.decision_deadline = datetime.now(timezone.utc) + timedelta(hours=3)  # a cut-off still ahead, so a decision waits for it
    db_session.commit()
    return item


def test_a_check_paid_on_a_reverse_account_is_a_duplicate_when_presented_again(db_session, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="settle-reverse")
    _reverse_customer(db_session, tenant, account)
    first = _present(db_session, tenant, account, "9001")
    assert first.exception_types == "review"
    _lock(db_session, _decide(db_session, tenant, first, users["admin"]), first)  # as the cut-off would, by silence
    assert _paid(db_session, first).settlement_status == PaidItemSettlementStatus.PAID

    again = _present(db_session, tenant, account, "9001")
    assert "duplicate_paid" in again.exception_types.split(",")  # a real problem: the customer's default, never silence


def test_an_accepted_amount_mismatch_pays_the_issued_check_and_a_second_presentment_is_a_duplicate(db_session, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="settle-positive")
    issued = issued_item_service.create_issued_item(db_session, tenant.id, issued_item_service.IssuedItemInput(
        account_id=account.id, check_number="5001", amount=Decimal("100.00"), payee_name="Acme", issue_date=date(2026, 10, 1)),
        submitted_by_user_id=None)
    db_session.commit()
    first = _present_positive(db_session, tenant, account, "5001", "120.00")
    assert first.exception_types == "amount_mismatch"
    result = _decide(db_session, tenant, first, users["admin"])
    assert _paid(db_session, first).settlement_status == PaidItemSettlementStatus.PENDING  # changeable until the cut-off: not yet
    _lock(db_session, result, first)
    assert _paid(db_session, first).settlement_status == PaidItemSettlementStatus.PAID
    assert db_session.get(type(issued), issued.id).status == IssuedItemStatus.PAID

    again = _present_positive(db_session, tenant, account, "5001", "120.00")
    assert "duplicate_paid" in again.exception_types.split(",")
    third = _present_positive(db_session, tenant, account, "5001", "100.00")  # the issued amount this time: the check is still paid
    assert "duplicate_paid" in third.exception_types.split(",")


def test_a_returned_check_is_returned_and_its_issued_check_stays_outstanding(db_session, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="settle-return")
    issued = issued_item_service.create_issued_item(db_session, tenant.id, issued_item_service.IssuedItemInput(
        account_id=account.id, check_number="5002", amount=Decimal("100.00"), payee_name="Acme", issue_date=date(2026, 10, 1)),
        submitted_by_user_id=None)
    db_session.commit()
    item = _present_positive(db_session, tenant, account, "5002", "120.00")
    _lock(db_session, _decide(db_session, tenant, item, users["admin"], outcome=DecisionOutcome.RETURN), item)
    assert _paid(db_session, item).settlement_status == PaidItemSettlementStatus.RETURNED
    assert db_session.get(type(issued), issued.id).status == IssuedItemStatus.OUTSTANDING


def test_paying_a_voided_check_keeps_the_customers_void(db_session, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="settle-voided")
    issued = issued_item_service.create_issued_item(db_session, tenant.id, issued_item_service.IssuedItemInput(
        account_id=account.id, check_number="5003", amount=Decimal("100.00"), payee_name="Acme", issue_date=date(2026, 10, 1)),
        submitted_by_user_id=None)
    issued_item_service.void_issued_item(db_session, tenant.id, issued.id, reason="Spoiled")
    db_session.commit()
    item = _present_positive(db_session, tenant, account, "5003", "100.00")
    assert item.exception_types == "voided"
    _lock(db_session, _decide(db_session, tenant, item, users["admin"]), item)
    assert _paid(db_session, item).settlement_status == PaidItemSettlementStatus.PAID
    assert db_session.get(type(issued), issued.id).status == IssuedItemStatus.VOIDED
    assert _present_positive(db_session, tenant, account, "5003", "100.00").exception_types.split(",").count("voided") == 1
