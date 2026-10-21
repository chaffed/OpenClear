# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from openclear.domain.ach_transaction import AchSettlementStatus, AchTransactionType
from openclear.domain.customer_disposition_setting import DispositionMode
from openclear.domain.decision import DecisionOutcome, DecisionSource
from openclear.domain.exception_item import ExceptionStatus
from openclear.domain.notification import Notification, NotificationType
from openclear.networks.ach.ingestion import AchTransactionSubmission, ingest_ach_transaction
from openclear.networks.check.ingestion import PaidItemSubmission, ingest_paid_item
from openclear.repositories.exception_repo import ExceptionRepository
from openclear.services import account_service, ach_return_reason_service, auto_disposition_service, customer_service, issued_item_service


def _make_customer(db_session, tenant, number="D-1"):
    customer = customer_service.create_customer(
        db_session, tenant.id, customer_service.CustomerInput(customer_number=number, name=f"Customer {number}")
    )
    account = account_service.create_account(
        db_session, tenant.id, account_service.AccountInput(account_number=f"{number}-ACCT", name="Op", customer_id=customer.id)
    )
    db_session.commit()
    return customer, account


def _mismatched_check_exception(db_session, tenant, account, users, check_number="D001"):
    issued_item_service.create_issued_item(
        db_session, tenant.id,
        issued_item_service.IssuedItemInput(
            account_id=account.id, check_number=check_number, amount=Decimal("100.00"), payee_name="X", issue_date=date(2026, 1, 1)
        ),
        submitted_by_user_id=users["preparer"].id,
    )
    db_session.commit()
    paid_item = ingest_paid_item(
        db_session, tenant.id,
        PaidItemSubmission(account_id=account.id, check_number=check_number, presented_amount=Decimal("999.00"), presented_date=date(2026, 1, 10)),
    )
    db_session.commit()
    exception = ExceptionRepository(db_session, tenant.id).list(source_item_id=paid_item.id)[0]
    return paid_item, exception


def _ach_exception(db_session, tenant, account, trace="D-TRACE-1"):
    txn = ingest_ach_transaction(
        db_session, tenant.id,
        AchTransactionSubmission(
            account_id=account.id, originator_id="UNKNOWNCO", originator_name="Unknown Co", amount=Decimal("75.00"),
            transaction_type=AchTransactionType.DEBIT, sec_code="WEB", trace_number=trace, effective_date=date(2026, 1, 10),
        ),
    )
    db_session.commit()
    exception = ExceptionRepository(db_session, tenant.id).list(source_item_id=txn.id)[0]
    return txn, exception


def test_get_disposition_mode_defaults_to_none(db_session, tenant_factory):
    tenant, _account, _users = tenant_factory.make(slug="disp-mode-default")
    customer, _account2 = _make_customer(db_session, tenant)

    assert auto_disposition_service.get_disposition_mode(db_session, tenant.id, customer.id, "check") == DispositionMode.NONE


def test_set_disposition_setting_upserts(db_session, tenant_factory):
    tenant, _account, _users = tenant_factory.make(slug="disp-mode-upsert")
    customer, _account2 = _make_customer(db_session, tenant)

    auto_disposition_service.set_disposition_setting(
        db_session, tenant.id, customer.id, "check",
        mode=DispositionMode.FIXED_PAY, response_window_hours=12, default_ach_return_reason_id=None,
    )
    db_session.commit()
    assert auto_disposition_service.get_disposition_mode(db_session, tenant.id, customer.id, "check") == DispositionMode.FIXED_PAY
    assert auto_disposition_service.get_response_window_hours(db_session, tenant.id, customer.id, "check") == 12

    auto_disposition_service.set_disposition_setting(
        db_session, tenant.id, customer.id, "check",
        mode=DispositionMode.FIXED_RETURN, response_window_hours=None, default_ach_return_reason_id=None,
    )
    db_session.commit()
    assert auto_disposition_service.get_disposition_mode(db_session, tenant.id, customer.id, "check") == DispositionMode.FIXED_RETURN
    from openclear.config import get_settings

    assert auto_disposition_service.get_response_window_hours(db_session, tenant.id, customer.id, "check") == get_settings().default_disposition_response_window_hours


def test_compute_decision_deadline_none_for_bank_wide_item(db_session, tenant_factory):
    tenant, _account, _users = tenant_factory.make(slug="disp-deadline-bankwide")

    assert auto_disposition_service.compute_decision_deadline(db_session, tenant.id, None, "check") is None


def test_compute_decision_deadline_none_when_mode_is_none(db_session, tenant_factory):
    tenant, _account, _users = tenant_factory.make(slug="disp-deadline-none-mode")
    customer, _account2 = _make_customer(db_session, tenant)

    assert auto_disposition_service.compute_decision_deadline(db_session, tenant.id, customer.id, "check") is None


def test_compute_decision_deadline_set_when_mode_configured(db_session, tenant_factory):
    tenant, _account, _users = tenant_factory.make(slug="disp-deadline-configured")
    customer, _account2 = _make_customer(db_session, tenant)
    auto_disposition_service.set_disposition_setting(
        db_session, tenant.id, customer.id, "check",
        mode=DispositionMode.FIXED_PAY, response_window_hours=6, default_ach_return_reason_id=None,
    )
    db_session.commit()

    before = datetime.now(timezone.utc)
    deadline = auto_disposition_service.compute_decision_deadline(db_session, tenant.id, customer.id, "check")
    assert deadline is not None
    assert timedelta(hours=5, minutes=59) < (deadline - before) <= timedelta(hours=6, minutes=1)


def test_ingestion_sets_decision_deadline_only_when_customer_opted_in(db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="disp-ingestion-opt-in")
    customer, account = _make_customer(db_session, tenant)

    _paid_item, exception_no_setting = _mismatched_check_exception(db_session, tenant, account, users, "D100")
    assert exception_no_setting.decision_deadline is None

    auto_disposition_service.set_disposition_setting(
        db_session, tenant.id, customer.id, "check",
        mode=DispositionMode.FIXED_PAY, response_window_hours=6, default_ach_return_reason_id=None,
    )
    db_session.commit()

    _paid_item2, exception_with_setting = _mismatched_check_exception(db_session, tenant, account, users, "D101")
    assert exception_with_setting.decision_deadline is not None


def test_auto_decide_exception_none_mode_returns_none(db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="disp-auto-decide-none")
    customer, account = _make_customer(db_session, tenant)
    _paid_item, exception = _mismatched_check_exception(db_session, tenant, account, users)

    result = auto_disposition_service.auto_decide_exception(db_session, exception)
    assert result is None
    assert exception.status == ExceptionStatus.OPEN


def test_auto_decide_exception_fixed_pay(db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="disp-auto-decide-fixed-pay")
    customer, account = _make_customer(db_session, tenant)
    _paid_item, exception = _mismatched_check_exception(db_session, tenant, account, users)
    auto_disposition_service.set_disposition_setting(
        db_session, tenant.id, customer.id, "check",
        mode=DispositionMode.FIXED_PAY, response_window_hours=1, default_ach_return_reason_id=None,
    )
    db_session.commit()

    decision = auto_disposition_service.auto_decide_exception(db_session, exception)
    db_session.commit()

    assert decision is not None
    assert decision.outcome == DecisionOutcome.PAY
    assert decision.source == DecisionSource.AUTO_DEFAULT
    assert decision.decided_by_user_id is None
    assert exception.status == ExceptionStatus.PAY

    notif = (
        db_session.query(Notification)
        .filter(Notification.notification_type == NotificationType.EXCEPTION_AUTO_DECIDED, Notification.resource_id == exception.id)
        .all()
    )
    assert len(notif) >= 1


def test_auto_decide_exception_ach_return_without_configured_reason_skips(db_session, tenant_factory):
    tenant, _account, _users = tenant_factory.make(slug="disp-auto-decide-ach-no-reason")
    customer, account = _make_customer(db_session, tenant)
    _txn, exception = _ach_exception(db_session, tenant, account)
    auto_disposition_service.set_disposition_setting(
        db_session, tenant.id, customer.id, "ach",
        mode=DispositionMode.FIXED_RETURN, response_window_hours=1, default_ach_return_reason_id=None,
    )
    db_session.commit()

    result = auto_disposition_service.auto_decide_exception(db_session, exception)
    assert result is None
    assert exception.status == ExceptionStatus.OPEN


def test_auto_decide_exception_ach_return_with_configured_reason_succeeds(db_session, tenant_factory):
    tenant, _account, _users = tenant_factory.make(slug="disp-auto-decide-ach-reason")
    customer, account = _make_customer(db_session, tenant)
    txn, exception = _ach_exception(db_session, tenant, account)
    reason = ach_return_reason_service.list_ach_return_reasons(db_session, tenant.id, active_only=True)[0]
    auto_disposition_service.set_disposition_setting(
        db_session, tenant.id, customer.id, "ach",
        mode=DispositionMode.FIXED_RETURN, response_window_hours=1, default_ach_return_reason_id=reason.id,
    )
    db_session.commit()

    decision = auto_disposition_service.auto_decide_exception(db_session, exception)
    db_session.commit()

    assert decision is not None
    assert decision.outcome == DecisionOutcome.RETURN
    assert decision.reason_code == reason.reason_text
    assert decision.return_transaction_code == reason.transaction_code
    db_session.expire_all()
    assert txn.settlement_status == AchSettlementStatus.RETURNED
