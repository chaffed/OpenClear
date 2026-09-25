# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Per-customer ML models: training/scoring scoped to one customer, the AUTO-mode
switch from global to a customer's own model once one is active, and the admin
GLOBAL/CUSTOMER overrides — see ml/train.py, ml/registry.py, ml/predict.py."""

import time
import uuid
from datetime import date
from decimal import Decimal

from openclear.db.tenancy import TenantContext
from openclear.domain.customer_ml_setting import MlScoringMode
from openclear.domain.decision import DecisionOutcome
from openclear.networks.check.ingestion import PaidItemSubmission, ingest_paid_item
from openclear.repositories.exception_repo import ExceptionRepository
from openclear.services import account_service, customer_service, decision_service, issued_item_service


def _make_customer_with_account(db_session, tenant, customer_number: str):
    customer = customer_service.create_customer(
        db_session, tenant.id, customer_service.CustomerInput(customer_number=customer_number, name=f"Customer {customer_number}")
    )
    account = account_service.create_account(
        db_session,
        tenant.id,
        account_service.AccountInput(account_number=f"{customer_number}-ACCT", name="Acct", customer_id=customer.id),
    )
    db_session.commit()
    return customer, account


def _make_exception(db_session, tenant, account, users, check_number: str, issued_amount: str, presented_amount: str):
    issued_item_service.create_issued_item(
        db_session,
        tenant.id,
        issued_item_service.IssuedItemInput(
            account_id=account.id,
            check_number=check_number,
            amount=Decimal(issued_amount),
            payee_name="Some Vendor",
            issue_date=date(2026, 1, 1),
        ),
        submitted_by_user_id=users["preparer"].id,
    )
    db_session.commit()

    paid_item = ingest_paid_item(
        db_session,
        tenant.id,
        PaidItemSubmission(
            account_id=account.id,
            check_number=check_number,
            presented_amount=Decimal(presented_amount),
            presented_date=date(2026, 1, 10),
        ),
    )
    db_session.commit()

    exceptions = ExceptionRepository(db_session, tenant.id).list(source_item_id=paid_item.id)
    return exceptions[0]


def _decide(db_session, tenant, users, exception, outcome: DecisionOutcome):
    ctx = TenantContext(
        tenant_id=tenant.id,
        user_id=users["approver"].id,
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
    result = decision_service.decide(
        db_session, tenant.id, exception.id, ctx, outcome=outcome, reason_code="test", notes=None
    )
    db_session.commit()
    return result


def _seed_decisions(db_session, tenant, account, users, count: int, prefix: str):
    for i in range(count):
        exception = _make_exception(
            db_session, tenant, account, users, f"{prefix}{i:02d}", "100.00", "999.00" if i % 2 == 0 else "888.00"
        )
        outcome = DecisionOutcome.RETURN if i % 2 == 0 else DecisionOutcome.PAY
        result = _decide(db_session, tenant, users, exception, outcome)
        assert result.error is None


def test_exception_denormalizes_customer_id_from_source_item(db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="ml-cust-denorm")
    customer, account = _make_customer_with_account(db_session, tenant, "C-1")

    exception = _make_exception(db_session, tenant, account, users, "1", "100.00", "999.00")

    assert exception.customer_id == customer.id


def test_train_model_scoped_to_customer_ignores_other_customers_decisions(db_session, tenant_factory):
    from openclear.ml.train import InsufficientTrainingData, train_model

    tenant, _account, users = tenant_factory.make(slug="ml-cust-scope")
    customer_a, account_a = _make_customer_with_account(db_session, tenant, "C-A")
    customer_b, account_b = _make_customer_with_account(db_session, tenant, "C-B")

    _seed_decisions(db_session, tenant, account_a, users, 12, "a")
    _seed_decisions(db_session, tenant, account_b, users, 3, "b")

    result_a = train_model(db_session, "check", customer_id=customer_a.id)
    assert result_a.model_row.trained_from_decision_count == 12
    assert result_a.model_row.customer_id == customer_a.id

    try:
        train_model(db_session, "check", customer_id=customer_b.id)
        assert False, "expected InsufficientTrainingData for customer B's 3 decisions"
    except InsufficientTrainingData:
        pass


def test_customer_model_and_global_model_are_independent_active_slots(db_session, tenant_factory):
    from openclear.ml.registry import get_active_model_row
    from openclear.ml.train import train_model

    tenant, account, users = tenant_factory.make(slug="ml-cust-slots")
    customer, customer_account = _make_customer_with_account(db_session, tenant, "C-1")

    _seed_decisions(db_session, tenant, account, users, 12, "g")
    _seed_decisions(db_session, tenant, customer_account, users, 12, "c")

    global_result = train_model(db_session, "check")
    customer_result = train_model(db_session, "check", customer_id=customer.id)

    global_active = get_active_model_row(db_session, "check")
    customer_active = get_active_model_row(db_session, "check", customer.id)

    assert global_active is not None and global_active.id == global_result.model_row.id
    assert customer_active is not None and customer_active.id == customer_result.model_row.id
    assert global_active.id != customer_active.id


def test_score_exception_auto_switches_to_customer_model_once_active(db_session, tenant_factory):
    from openclear.ml.predict import reset_model_cache, score_exception
    from openclear.ml.train import train_model

    reset_model_cache()
    tenant, account, users = tenant_factory.make(slug="ml-cust-autoswitch")
    customer, customer_account = _make_customer_with_account(db_session, tenant, "C-1")

    _seed_decisions(db_session, tenant, account, users, 12, "g")
    _seed_decisions(db_session, tenant, customer_account, users, 12, "c")

    global_result = train_model(db_session, "check")
    # version strings are timestamp-based at second resolution, and each scope starts
    # its own "v1" numbering — sleep past the second boundary so the two versions below
    # are guaranteed distinct, not just coincidentally so.
    time.sleep(1.05)
    customer_result = train_model(db_session, "check", customer_id=customer.id)
    assert global_result.model_row.id != customer_result.model_row.id
    assert global_result.model_row.version != customer_result.model_row.version

    customer_exception = _make_exception(db_session, tenant, customer_account, users, "new-c", "100.00", "999.00")
    bank_wide_exception = _make_exception(db_session, tenant, account, users, "new-g", "100.00", "999.00")

    score_exception(db_session, customer_exception)
    score_exception(db_session, bank_wide_exception)

    assert customer_exception.ml_model_version == customer_result.model_row.version
    assert bank_wide_exception.ml_model_version == global_result.model_row.version


def test_mode_global_override_forces_global_scoring_even_with_active_customer_model(db_session, tenant_factory):
    from openclear.ml.predict import reset_model_cache, score_exception
    from openclear.ml.train import train_model
    from openclear.services import customer_ml_service

    reset_model_cache()
    tenant, account, users = tenant_factory.make(slug="ml-cust-globaloverride")
    customer, customer_account = _make_customer_with_account(db_session, tenant, "C-1")

    _seed_decisions(db_session, tenant, account, users, 12, "g")
    _seed_decisions(db_session, tenant, customer_account, users, 12, "c")
    global_result = train_model(db_session, "check")
    train_model(db_session, "check", customer_id=customer.id)

    customer_ml_service.set_mode(db_session, tenant.id, customer.id, "check", MlScoringMode.GLOBAL)
    db_session.commit()

    new_exception = _make_exception(db_session, tenant, customer_account, users, "override", "100.00", "999.00")
    score_exception(db_session, new_exception)

    assert new_exception.ml_model_version == global_result.model_row.version


def test_mode_customer_falls_back_to_global_when_no_customer_model_trained_yet(db_session, tenant_factory):
    from openclear.ml.predict import reset_model_cache, score_exception
    from openclear.ml.train import train_model
    from openclear.services import customer_ml_service

    reset_model_cache()
    tenant, account, users = tenant_factory.make(slug="ml-cust-fallback")
    customer, customer_account = _make_customer_with_account(db_session, tenant, "C-1")

    _seed_decisions(db_session, tenant, account, users, 12, "g")
    global_result = train_model(db_session, "check")

    customer_ml_service.set_mode(db_session, tenant.id, customer.id, "check", MlScoringMode.CUSTOMER)
    db_session.commit()

    new_exception = _make_exception(db_session, tenant, customer_account, users, "fallback", "100.00", "999.00")
    score = score_exception(db_session, new_exception)

    assert score is not None
    assert new_exception.ml_model_version == global_result.model_row.version


def test_retrain_job_trains_a_customer_model_once_enough_customer_decisions_exist(db_session, tenant_factory, monkeypatch):
    import openclear.workers.tasks as tasks_module
    from openclear.config import get_settings
    from openclear.ml.registry import get_active_model_row

    tenant, _account, users = tenant_factory.make(slug="ml-cust-retrainjob")
    customer, customer_account = _make_customer_with_account(db_session, tenant, "C-1")
    _seed_decisions(db_session, tenant, customer_account, users, 12, "j")

    # See test_train_and_predict.py::test_retrain_job_skips_networks_below_threshold's
    # comment — patch the name as workers/tasks.py itself references it, not
    # openclear.db.session's own attribute.
    monkeypatch.setattr(tasks_module, "get_session_factory", lambda: lambda: db_session)
    original_threshold = get_settings().ml_min_new_decisions_for_retrain
    get_settings().ml_min_new_decisions_for_retrain = 10
    try:
        tasks_module.retrain_job()
    finally:
        get_settings().ml_min_new_decisions_for_retrain = original_threshold

    customer_model = get_active_model_row(db_session, "check", customer.id)
    assert customer_model is not None
    assert customer_model.trained_from_decision_count == 12
