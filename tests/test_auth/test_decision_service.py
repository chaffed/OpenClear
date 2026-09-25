# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import uuid
from datetime import date
from decimal import Decimal

from openclear.db.tenancy import TenantContext
from openclear.domain.ach_transaction import AchSettlementStatus, AchTransactionType
from openclear.domain.decision import DecisionOutcome
from openclear.domain.exception_item import ExceptionStatus
from openclear.networks.ach.ingestion import AchTransactionSubmission, ingest_ach_transaction
from openclear.networks.check.ingestion import PaidItemSubmission, ingest_paid_item
from openclear.repositories.exception_repo import ExceptionRepository
from openclear.services import ach_return_reason_service, decision_service, issued_item_service
from openclear.services.decision_service import DecisionError


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


def _make_ach_exception(db_session, tenant, account, check_number_suffix="1"):
    txn = ingest_ach_transaction(
        db_session,
        tenant.id,
        AchTransactionSubmission(
            account_id=account.id,
            originator_id=f"UNKNOWN{check_number_suffix}",
            originator_name="Suspicious LLC",
            amount=Decimal("75.00"),
            transaction_type=AchTransactionType.DEBIT,
            sec_code="WEB",
            trace_number=f"TRACE{check_number_suffix}",
            effective_date=date(2026, 1, 10),
        ),
    )
    db_session.commit()
    exceptions = ExceptionRepository(db_session, tenant.id).list(source_item_id=txn.id)
    return txn, exceptions[0]


def _make_check_exception(db_session, tenant, account, users, check_number="9001"):
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
        PaidItemSubmission(account_id=account.id, check_number=check_number, presented_amount=Decimal("150.00"), presented_date=date(2026, 1, 10)),
    )
    db_session.commit()
    exceptions = ExceptionRepository(db_session, tenant.id).list(source_item_id=paid_item.id)
    return exceptions[0]


def test_ach_return_without_reason_id_is_rejected(db_session, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="decide-ach-no-reason")
    _txn, exception = _make_ach_exception(db_session, tenant, account)

    result = decision_service.decide(
        db_session, tenant.id, exception.id, _ctx(tenant, users["approver"].id),
        outcome=DecisionOutcome.RETURN, reason_code="whatever", notes=None,
    )

    assert result.error == DecisionError.RETURN_REASON_REQUIRED


def test_ach_return_with_invalid_reason_id_is_rejected(db_session, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="decide-ach-bad-reason")
    _txn, exception = _make_ach_exception(db_session, tenant, account)

    result = decision_service.decide(
        db_session, tenant.id, exception.id, _ctx(tenant, users["approver"].id),
        outcome=DecisionOutcome.RETURN, reason_code="whatever", notes=None,
        ach_return_reason_id=uuid.uuid4(),
    )

    assert result.error == DecisionError.INVALID_RETURN_REASON


def test_ach_return_with_deactivated_reason_is_rejected(db_session, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="decide-ach-deactivated-reason")
    _txn, exception = _make_ach_exception(db_session, tenant, account)
    reason = ach_return_reason_service.list_ach_return_reasons(db_session, tenant.id, active_only=True)[0]
    ach_return_reason_service.deactivate_ach_return_reason(db_session, tenant.id, reason.id)
    db_session.commit()

    result = decision_service.decide(
        db_session, tenant.id, exception.id, _ctx(tenant, users["approver"].id),
        outcome=DecisionOutcome.RETURN, reason_code="whatever", notes=None,
        ach_return_reason_id=reason.id,
    )

    assert result.error == DecisionError.INVALID_RETURN_REASON


def test_ach_return_with_valid_reason_snapshots_reason_and_trancode(db_session, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="decide-ach-valid-reason")
    txn, exception = _make_ach_exception(db_session, tenant, account)
    reason = ach_return_reason_service.create_ach_return_reason(
        db_session, tenant.id, ach_return_reason_service.AchReturnReasonInput(reason_text="Customer Advises Not Authorized (custom)", transaction_code="667")
    )
    db_session.commit()

    result = decision_service.decide(
        db_session, tenant.id, exception.id, _ctx(tenant, users["approver"].id),
        outcome=DecisionOutcome.RETURN, reason_code="ignored freeform text", notes=None,
        ach_return_reason_id=reason.id,
    )

    assert result.error is None
    assert result.decision.reason_code == reason.reason_text
    assert result.decision.return_transaction_code == "667"
    assert result.exception.status == ExceptionStatus.RETURN


def test_ach_return_sets_underlying_transaction_settlement_status_returned(db_session, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="decide-ach-settlement-status")
    txn, exception = _make_ach_exception(db_session, tenant, account)
    reason = ach_return_reason_service.list_ach_return_reasons(db_session, tenant.id, active_only=True)[0]

    assert txn.settlement_status != AchSettlementStatus.RETURNED

    result = decision_service.decide(
        db_session, tenant.id, exception.id, _ctx(tenant, users["approver"].id),
        outcome=DecisionOutcome.RETURN, reason_code="x", notes=None, ach_return_reason_id=reason.id,
    )
    db_session.commit()

    assert result.error is None
    db_session.expire_all()
    assert txn.settlement_status == AchSettlementStatus.RETURNED


def test_ach_pay_outcome_does_not_require_return_reason(db_session, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="decide-ach-pay-no-reason")
    _txn, exception = _make_ach_exception(db_session, tenant, account)

    result = decision_service.decide(
        db_session, tenant.id, exception.id, _ctx(tenant, users["approver"].id),
        outcome=DecisionOutcome.PAY, reason_code="verified with originator", notes=None,
    )

    assert result.error is None
    assert result.decision.reason_code == "verified with originator"
    assert result.decision.return_transaction_code is None


def test_check_network_return_unaffected_by_ach_return_reason_requirement(db_session, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="decide-check-return-unaffected")
    exception = _make_check_exception(db_session, tenant, account, users)

    result = decision_service.decide(
        db_session, tenant.id, exception.id, _ctx(tenant, users["approver"].id),
        outcome=DecisionOutcome.RETURN, reason_code="confirmed_fraud", notes=None,
    )

    assert result.error is None
    assert result.decision.reason_code == "confirmed_fraud"
    assert result.decision.return_transaction_code is None


def test_submit_recommendation_also_requires_return_reason_for_ach_return(db_session, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="recommend-ach-no-reason")
    _txn, exception = _make_ach_exception(db_session, tenant, account)

    result = decision_service.submit_recommendation(
        db_session, tenant.id, exception.id, _ctx(tenant, users["preparer"].id),
        outcome=DecisionOutcome.RETURN, reason_code="whatever", notes=None,
    )

    assert result.error == DecisionError.RETURN_REASON_REQUIRED


def test_submit_recommendation_snapshots_return_reason(db_session, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="recommend-ach-valid-reason")
    _txn, exception = _make_ach_exception(db_session, tenant, account)
    reason = ach_return_reason_service.list_ach_return_reasons(db_session, tenant.id, active_only=True)[0]

    result = decision_service.submit_recommendation(
        db_session, tenant.id, exception.id, _ctx(tenant, users["preparer"].id),
        outcome=DecisionOutcome.RETURN, reason_code="ignored", notes=None, ach_return_reason_id=reason.id,
    )

    assert result.error is None
    assert result.exception.recommended_reason_code == reason.reason_text
    assert result.exception.recommended_return_transaction_code == reason.transaction_code
