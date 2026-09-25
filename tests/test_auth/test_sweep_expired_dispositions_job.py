# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from openclear.domain.customer_disposition_setting import DispositionMode
from openclear.domain.exception_item import ExceptionStatus
from openclear.networks.check.ingestion import PaidItemSubmission, ingest_paid_item
from openclear.repositories.exception_repo import ExceptionRepository
from openclear.services import account_service, auto_disposition_service, customer_service, issued_item_service
from openclear.workers import tasks


def _make_customer(db_session, tenant, number="S-1"):
    customer = customer_service.create_customer(
        db_session, tenant.id, customer_service.CustomerInput(customer_number=number, name=f"Customer {number}")
    )
    account = account_service.create_account(
        db_session, tenant.id, account_service.AccountInput(account_number=f"{number}-ACCT", name="Op", customer_id=customer.id)
    )
    db_session.commit()
    return customer, account


def _expired_exception(db_session, tenant, account, users, check_number):
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
    # Force the deadline into the past regardless of what compute_decision_deadline set.
    exception.decision_deadline = datetime.now(timezone.utc) - timedelta(hours=1)
    db_session.commit()
    return exception


def test_sweep_auto_decides_configured_exception(monkeypatch, session_factory, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="sweep-decides")
    customer, account = _make_customer(db_session, tenant)
    auto_disposition_service.set_disposition_setting(
        db_session, tenant.id, customer.id, "check",
        mode=DispositionMode.FIXED_PAY, response_window_hours=1, default_ach_return_reason_id=None,
    )
    db_session.commit()
    exception = _expired_exception(db_session, tenant, account, users, "S001")

    monkeypatch.setattr(tasks, "get_session_factory", lambda: session_factory)
    tasks.sweep_expired_dispositions_job()

    db_session.expire_all()
    db_session.refresh(exception)
    assert exception.status == ExceptionStatus.PAY


def test_sweep_leaves_unconfigured_exception_open(monkeypatch, session_factory, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="sweep-skips")
    _customer, account = _make_customer(db_session, tenant)
    exception = _expired_exception(db_session, tenant, account, users, "S002")

    monkeypatch.setattr(tasks, "get_session_factory", lambda: session_factory)
    tasks.sweep_expired_dispositions_job()

    db_session.expire_all()
    db_session.refresh(exception)
    assert exception.status == ExceptionStatus.OPEN


def test_sweep_ignores_exceptions_not_yet_expired(monkeypatch, session_factory, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="sweep-not-expired")
    customer, account = _make_customer(db_session, tenant)
    auto_disposition_service.set_disposition_setting(
        db_session, tenant.id, customer.id, "check",
        mode=DispositionMode.FIXED_PAY, response_window_hours=1, default_ach_return_reason_id=None,
    )
    db_session.commit()
    exception = _expired_exception(db_session, tenant, account, users, "S003")
    exception.decision_deadline = datetime.now(timezone.utc) + timedelta(hours=1)
    db_session.commit()

    monkeypatch.setattr(tasks, "get_session_factory", lambda: session_factory)
    tasks.sweep_expired_dispositions_job()

    db_session.expire_all()
    db_session.refresh(exception)
    assert exception.status == ExceptionStatus.OPEN
