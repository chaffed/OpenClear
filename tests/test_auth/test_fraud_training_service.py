# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import uuid
from datetime import date
from decimal import Decimal

import pytest

from openclear.db.tenancy import TenantContext
from openclear.domain.ach_transaction import AchTransactionType
from openclear.domain.decision import DecisionOutcome
from openclear.domain.exception_item import ExceptionItemSource, ExceptionStatus
from openclear.networks.ach.ingestion import AchTransactionSubmission, ingest_ach_transaction
from openclear.networks.check.ingestion import PaidItemSubmission, ingest_paid_item
from openclear.repositories.exception_repo import ExceptionRepository
from openclear.services import ach_return_reason_service, decision_service, issued_item_service
from openclear.services.fraud_training_service import (
    AchFraudRawInput,
    CheckFraudRawInput,
    FraudTrainingError,
    retract_fraud_example,
    submit_ach_fraud_example,
    submit_check_fraud_example,
)


def _ctx(tenant, user_id):
    return TenantContext(
        tenant_id=tenant.id,
        user_id=user_id,
        security_group_id=uuid.uuid4(),
        permissions=frozenset(),
        tenant_slug=tenant.slug,
        tenant_name=tenant.name,
        accent_color=None,
        has_logo=False,
        has_favicon=False,
        customer_id=None,
        customer_name=None,
    )


def _clean_match_paid_item(db_session, tenant, account, users, check_number="7001"):
    """A presented check that matches its issued item cleanly (same amount, same day) —
    never becomes an exception through the real pipeline, so it's the case
    submit_check_fraud_example's attach mode exists to handle."""
    issued_item_service.create_issued_item(
        db_session,
        tenant.id,
        issued_item_service.IssuedItemInput(
            account_id=account.id, check_number=check_number, amount=Decimal("100.00"), payee_name="X", issue_date=date(2026, 1, 1)
        ),
        submitted_by_user_id=users["preparer"].id,
    )
    db_session.commit()
    paid_item = ingest_paid_item(
        db_session,
        tenant.id,
        PaidItemSubmission(account_id=account.id, check_number=check_number, presented_amount=Decimal("100.00"), presented_date=date(2026, 1, 1)),
    )
    db_session.commit()
    return paid_item


def _mismatched_paid_item_exception(db_session, tenant, account, users, check_number="7002"):
    issued_item_service.create_issued_item(
        db_session,
        tenant.id,
        issued_item_service.IssuedItemInput(
            account_id=account.id, check_number=check_number, amount=Decimal("100.00"), payee_name="X", issue_date=date(2026, 1, 1)
        ),
        submitted_by_user_id=users["preparer"].id,
    )
    db_session.commit()
    paid_item = ingest_paid_item(
        db_session,
        tenant.id,
        PaidItemSubmission(account_id=account.id, check_number=check_number, presented_amount=Decimal("999.00"), presented_date=date(2026, 1, 10)),
    )
    db_session.commit()
    exceptions = ExceptionRepository(db_session, tenant.id).list(source_item_id=paid_item.id)
    return paid_item, exceptions[0]


def _clean_match_ach_transaction(db_session, tenant, account, trace="TRACE-CLEAN-1"):
    """No authorization rule exists for this originator, so it hits NOT_AUTHORIZED... to
    get a clean MATCH we'd need an AchAuthorizationRule; simpler here to just submit and
    assert it becomes an exception isn't needed — attach-mode tests below use whatever
    txn.match_status results, exercising the "already exists, whatever its status" path."""
    txn = ingest_ach_transaction(
        db_session,
        tenant.id,
        AchTransactionSubmission(
            account_id=account.id,
            originator_id="ORIGX",
            originator_name="Some Originator",
            amount=Decimal("50.00"),
            transaction_type=AchTransactionType.DEBIT,
            sec_code="WEB",
            trace_number=trace,
            effective_date=date(2026, 1, 10),
        ),
    )
    db_session.commit()
    return txn


def test_submit_check_fraud_example_raw_entry_creates_backfilled_decision(db_session, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="fraud-check-raw")
    ctx = _ctx(tenant, users["admin"].id)

    exception_item = submit_check_fraud_example(
        db_session, tenant.id, ctx,
        new_item=CheckFraudRawInput(account_id=account.id, check_number="RAW1", presented_amount=Decimal("500.00"), presented_date=date(2026, 2, 1)),
        reason_code="confirmed fraud ring",
    )
    db_session.commit()

    assert exception_item.source == ExceptionItemSource.TRAINING_BACKFILL
    assert exception_item.status == ExceptionStatus.RETURN
    assert exception_item.is_correction is False
    decision = decision_service.get_decision_for_exception(db_session, tenant.id, exception_item.id)
    assert decision.outcome == DecisionOutcome.RETURN
    assert decision.reason_code == "confirmed fraud ring"
    assert decision.features_json is not None
    assert decision.decided_by_user_id == users["admin"].id
    assert decision.submitted_by_user_id is None


def test_submit_check_fraud_example_requires_exactly_one_of_existing_or_new(db_session, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="fraud-check-both")
    ctx = _ctx(tenant, users["admin"].id)

    with pytest.raises(FraudTrainingError):
        submit_check_fraud_example(db_session, tenant.id, ctx, reason_code="x")

    with pytest.raises(FraudTrainingError):
        submit_check_fraud_example(
            db_session, tenant.id, ctx,
            existing_paid_item_id=uuid.uuid4(),
            new_item=CheckFraudRawInput(account_id=account.id, check_number="X", presented_amount=Decimal("1"), presented_date=date(2026, 1, 1)),
            reason_code="x",
        )


def test_submit_check_fraud_example_attaches_to_cleanly_matched_item(db_session, tenant_factory):
    """The high-value case: a check that cleared with no exception at all, later confirmed
    fraudulent — attach mode must not require it to have ever been an exception."""
    tenant, account, users = tenant_factory.make(slug="fraud-check-attach-clean")
    ctx = _ctx(tenant, users["admin"].id)
    paid_item = _clean_match_paid_item(db_session, tenant, account, users)

    exception_item = submit_check_fraud_example(
        db_session, tenant.id, ctx, existing_paid_item_id=paid_item.id, reason_code="cleared clean, confirmed fraud later"
    )
    db_session.commit()

    assert exception_item.source_item_id == paid_item.id
    assert exception_item.is_correction is False
    assert exception_item.status == ExceptionStatus.RETURN


def test_submit_check_fraud_example_flags_correction_when_already_live_decided(db_session, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="fraud-check-correction")
    ctx = _ctx(tenant, users["admin"].id)
    paid_item, exception = _mismatched_paid_item_exception(db_session, tenant, account, users)

    result = decision_service.decide(
        db_session, tenant.id, exception.id, _ctx(tenant, users["approver"].id),
        outcome=DecisionOutcome.PAY, reason_code="looked fine", notes=None,
    )
    db_session.commit()
    assert result.error is None

    correction = submit_check_fraud_example(
        db_session, tenant.id, ctx, existing_paid_item_id=paid_item.id, reason_code="turned out to be fraud"
    )
    db_session.commit()

    assert correction.is_correction is True
    assert correction.id != exception.id


def test_submit_check_fraud_example_rejects_double_labeling(db_session, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="fraud-check-duplicate")
    ctx = _ctx(tenant, users["admin"].id)
    paid_item = _clean_match_paid_item(db_session, tenant, account, users)

    submit_check_fraud_example(db_session, tenant.id, ctx, existing_paid_item_id=paid_item.id, reason_code="first")
    db_session.commit()

    with pytest.raises(FraudTrainingError):
        submit_check_fraud_example(db_session, tenant.id, ctx, existing_paid_item_id=paid_item.id, reason_code="second")


def test_submit_check_fraud_example_unknown_paid_item_raises(db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="fraud-check-unknown")
    ctx = _ctx(tenant, users["admin"].id)

    with pytest.raises(FraudTrainingError):
        submit_check_fraud_example(db_session, tenant.id, ctx, existing_paid_item_id=uuid.uuid4(), reason_code="x")


def test_submit_ach_fraud_example_raw_entry(db_session, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="fraud-ach-raw")
    ctx = _ctx(tenant, users["admin"].id)
    reason = ach_return_reason_service.list_ach_return_reasons(db_session, tenant.id, active_only=True)[0]

    exception_item = submit_ach_fraud_example(
        db_session, tenant.id, ctx,
        new_item=AchFraudRawInput(
            account_id=account.id, originator_id="FRAUDCO", originator_name="Fraud Co", amount=Decimal("999.00"),
            transaction_type=AchTransactionType.DEBIT, sec_code="WEB", trace_number="FRAUDTRACE1", effective_date=date(2026, 2, 1),
        ),
        ach_return_reason_id=reason.id,
    )
    db_session.commit()

    assert exception_item.source == ExceptionItemSource.TRAINING_BACKFILL
    assert exception_item.status == ExceptionStatus.RETURN
    decision = decision_service.get_decision_for_exception(db_session, tenant.id, exception_item.id)
    assert decision.outcome == DecisionOutcome.RETURN
    assert decision.reason_code == reason.reason_text
    assert decision.return_transaction_code == reason.transaction_code


def test_submit_ach_fraud_example_invalid_return_reason_rejected(db_session, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="fraud-ach-bad-reason")
    ctx = _ctx(tenant, users["admin"].id)

    with pytest.raises(FraudTrainingError):
        submit_ach_fraud_example(
            db_session, tenant.id, ctx,
            new_item=AchFraudRawInput(
                account_id=account.id, originator_id="X", originator_name="X", amount=Decimal("1"),
                transaction_type=AchTransactionType.DEBIT, sec_code="WEB", trace_number="BADREASON1", effective_date=date(2026, 1, 1),
            ),
            ach_return_reason_id=uuid.uuid4(),
        )


def test_submit_ach_fraud_example_attaches_to_existing_transaction(db_session, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="fraud-ach-attach")
    ctx = _ctx(tenant, users["admin"].id)
    reason = ach_return_reason_service.list_ach_return_reasons(db_session, tenant.id, active_only=True)[0]
    txn = _clean_match_ach_transaction(db_session, tenant, account)

    exception_item = submit_ach_fraud_example(
        db_session, tenant.id, ctx, existing_ach_transaction_id=txn.id, ach_return_reason_id=reason.id
    )
    db_session.commit()

    assert exception_item.source_item_id == txn.id
    db_session.expire_all()
    from openclear.domain.ach_transaction import AchSettlementStatus

    assert txn.settlement_status == AchSettlementStatus.RETURNED


def test_retract_fraud_example_success(db_session, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="fraud-retract-ok")
    ctx = _ctx(tenant, users["admin"].id)
    paid_item = _clean_match_paid_item(db_session, tenant, account, users)
    exception_item = submit_check_fraud_example(db_session, tenant.id, ctx, existing_paid_item_id=paid_item.id, reason_code="x")
    db_session.commit()

    retracted = retract_fraud_example(db_session, tenant.id, ctx, exception_item.id)
    db_session.commit()

    assert retracted.retracted_at is not None
    assert retracted.retracted_by_user_id == users["admin"].id


def test_retract_fraud_example_rejects_double_retract(db_session, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="fraud-retract-twice")
    ctx = _ctx(tenant, users["admin"].id)
    paid_item = _clean_match_paid_item(db_session, tenant, account, users)
    exception_item = submit_check_fraud_example(db_session, tenant.id, ctx, existing_paid_item_id=paid_item.id, reason_code="x")
    db_session.commit()
    retract_fraud_example(db_session, tenant.id, ctx, exception_item.id)
    db_session.commit()

    with pytest.raises(FraudTrainingError):
        retract_fraud_example(db_session, tenant.id, ctx, exception_item.id)


def test_retract_fraud_example_rejects_live_exception(db_session, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="fraud-retract-live")
    ctx = _ctx(tenant, users["admin"].id)
    _paid_item, exception = _mismatched_paid_item_exception(db_session, tenant, account, users)
    decision_service.decide(
        db_session, tenant.id, exception.id, _ctx(tenant, users["approver"].id),
        outcome=DecisionOutcome.PAY, reason_code="x", notes=None,
    )
    db_session.commit()

    with pytest.raises(FraudTrainingError):
        retract_fraud_example(db_session, tenant.id, ctx, exception.id)


def test_retract_fraud_example_unknown_id_returns_none(db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="fraud-retract-unknown")
    ctx = _ctx(tenant, users["admin"].id)

    assert retract_fraud_example(db_session, tenant.id, ctx, uuid.uuid4()) is None
