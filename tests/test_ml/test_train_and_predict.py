# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import uuid
from datetime import date
from decimal import Decimal

import pytest

from openclear.db.tenancy import TenantContext
from openclear.domain.decision import DecisionOutcome
from openclear.networks.check.ingestion import PaidItemSubmission, ingest_paid_item
from openclear.services import decision_service, issued_item_service


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

    from openclear.repositories.exception_repo import ExceptionRepository

    exceptions = ExceptionRepository(db_session, tenant.id).list(source_item_id=paid_item.id)
    return exceptions[0]


def _decide(db_session, tenant, users, exception, outcome: DecisionOutcome):
    # decision_service.decide only reads ctx.tenant_id/user_id (not permissions or
    # branding), so placeholder values are fine — this ctx isn't going through a
    # require_permission() check or rendered in a template.
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


def test_train_model_raises_below_minimum_decision_count(db_session, tenant_factory):
    from openclear.ml.train import InsufficientTrainingData, train_model

    tenant, _account, _users = tenant_factory.make(slug="ml-insufficient")

    with pytest.raises(InsufficientTrainingData):
        train_model(db_session, "check")


def test_score_exception_returns_none_before_any_model_trained(db_session, tenant_factory):
    from openclear.ml.predict import reset_model_cache, score_exception

    reset_model_cache()
    tenant, account, users = tenant_factory.make(slug="ml-cold-start")
    exception = _make_exception(db_session, tenant, account, users, "9101", "100.00", "999.00")

    score = score_exception(db_session, exception)

    assert score is None
    assert exception.ml_score is None


def test_train_then_score_populates_ml_score_on_new_exceptions(db_session, tenant_factory):
    from openclear.ml.predict import reset_model_cache, score_exception
    from openclear.ml.train import train_model

    reset_model_cache()
    tenant, account, users = tenant_factory.make(slug="ml-train-score", require_dual_control=False)

    # Generate 12 labeled decisions (above MIN_DECISIONS_TO_TRAIN=10), alternating
    # outcomes so both classes are present for a meaningful holdout split.
    for i in range(12):
        exception = _make_exception(
            db_session, tenant, account, users, f"92{i:02d}", "100.00", "999.00" if i % 2 == 0 else "888.00"
        )
        outcome = DecisionOutcome.RETURN if i % 2 == 0 else DecisionOutcome.PAY
        result = _decide(db_session, tenant, users, exception, outcome)
        assert result.error is None

    train_result = train_model(db_session, "check")
    assert train_result.promoted is True
    assert train_result.model_row.trained_from_decision_count == 12

    new_exception = _make_exception(db_session, tenant, account, users, "9301", "100.00", "999.00")
    score = score_exception(db_session, new_exception)

    assert score is not None
    assert 0.0 <= score <= 1.0
    assert new_exception.ml_model_version == train_result.model_row.version


def test_retracted_backfilled_decision_excluded_from_training(db_session, tenant_factory, monkeypatch):
    from openclear.config import get_settings
    from openclear.ml.train import InsufficientTrainingData, train_model
    from openclear.services.fraud_training_service import CheckFraudRawInput, retract_fraud_example, submit_check_fraud_example

    monkeypatch.setattr(get_settings(), "ml_retrain_cooldown_seconds", 0)
    tenant, account, users = tenant_factory.make(slug="ml-retract-excluded", require_dual_control=False)
    ctx = TenantContext(
        tenant_id=tenant.id,
        user_id=users["admin"].id,
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

    # 9 live decisions plus 1 backfilled one = 10, exactly MIN_DECISIONS_TO_TRAIN.
    for i in range(9):
        exception = _make_exception(db_session, tenant, account, users, f"95{i:02d}", "100.00", "999.00" if i % 2 == 0 else "888.00")
        outcome = DecisionOutcome.RETURN if i % 2 == 0 else DecisionOutcome.PAY
        _decide(db_session, tenant, users, exception, outcome)
    backfilled = submit_check_fraud_example(
        db_session, tenant.id, ctx,
        new_item=CheckFraudRawInput(account_id=account.id, check_number="9599", presented_amount=Decimal("500.00"), presented_date=date(2026, 1, 15)),
        reason_code="known fraud",
    )
    # A fraud-training example only feeds the SHARED model once the platform operator
    # has approved it (ml/train.py::_load_labeled_decisions).
    from datetime import datetime, timezone

    backfilled.shared_training_approved_at = datetime.now(timezone.utc)
    db_session.commit()

    train_result = train_model(db_session, "check")
    assert train_result.model_row.trained_from_decision_count == 10

    retract_fraud_example(db_session, tenant.id, ctx, backfilled.id)
    db_session.commit()

    with pytest.raises(InsufficientTrainingData):
        train_model(db_session, "check")


def test_retrain_job_skips_networks_below_threshold(db_session, tenant_factory, monkeypatch):
    import openclear.workers.tasks as tasks_module

    tenant, account, users = tenant_factory.make(slug="ml-retrain-job-skip")
    _make_exception(db_session, tenant, account, users, "9401", "100.00", "999.00")

    # Only 1 decision exists, well below the default threshold (20) — must not raise,
    # just skip. Bind the job to the same in-memory engine the test uses: patch the name
    # as workers/tasks.py itself references it (`from openclear.db.session import
    # get_session_factory`), not openclear.db.session's own attribute — a `from X import Y`
    # binds Y directly into this module's namespace, so patching X.Y afterward doesn't
    # affect what workers.tasks already has bound to that name.
    monkeypatch.setattr(tasks_module, "get_session_factory", lambda: lambda: db_session)
    tasks_module.retrain_job()  # should complete without raising despite no active model / low data


def test_retrain_cooldown_rejects_immediate_reretrain(db_session, tenant_factory):
    from openclear.ml.train import RetrainCooldownActive, train_model

    tenant, account, users = tenant_factory.make(slug="ml-cooldown", require_dual_control=False)
    for i in range(12):
        exception = _make_exception(db_session, tenant, account, users, f"93{i:02d}", "100.00", "999.00" if i % 2 == 0 else "888.00")
        outcome = DecisionOutcome.RETURN if i % 2 == 0 else DecisionOutcome.PAY
        _decide(db_session, tenant, users, exception, outcome)

    train_model(db_session, "check")

    with pytest.raises(RetrainCooldownActive):
        train_model(db_session, "check")


def test_retrain_cooldown_lifts_after_the_window_elapses(db_session, tenant_factory, monkeypatch):
    from openclear.config import get_settings
    from openclear.ml.train import train_model

    monkeypatch.setattr(get_settings(), "ml_retrain_cooldown_seconds", 0)
    tenant, account, users = tenant_factory.make(slug="ml-cooldown-lifted", require_dual_control=False)
    for i in range(12):
        exception = _make_exception(db_session, tenant, account, users, f"94{i:02d}", "100.00", "999.00" if i % 2 == 0 else "888.00")
        outcome = DecisionOutcome.RETURN if i % 2 == 0 else DecisionOutcome.PAY
        _decide(db_session, tenant, users, exception, outcome)

    train_model(db_session, "check")
    second = train_model(db_session, "check")  # cooldown is 0s, so this must succeed, not raise
    assert second.model_row.version != None


def test_retrain_job_continues_and_records_failure_after_unexpected_error(db_session, tenant_factory, monkeypatch):
    import openclear.workers.tasks as tasks_module
    from openclear.domain.ml_model import MlModel, MlModelStatus
    from openclear.ml.model import LogisticRegressionScoringModel
    from openclear.ml.registry import get_active_model_row
    from openclear.services import account_service, customer_service
    from sqlalchemy import select

    tenant, account, users = tenant_factory.make(slug="ml-retrain-resilience")
    customer = customer_service.create_customer(db_session, tenant.id, customer_service.CustomerInput(customer_number="C-1", name="Acme"))
    customer_account = account_service.create_account(
        db_session, tenant.id, account_service.AccountInput(account_number="9999", name="Cust Acct", customer_id=customer.id)
    )

    # retrain_job()'s own per-scope gate (settings.ml_min_new_decisions_for_retrain,
    # default 20) is stricter than train_model's internal minimum (10) — need enough of
    # each so BOTH the global and the customer-scoped retrain actually get attempted.
    for i in range(22):
        exception = _make_exception(db_session, tenant, account, users, f"81{i:02d}", "100.00", "999.00" if i % 2 == 0 else "888.00")
        outcome = DecisionOutcome.RETURN if i % 2 == 0 else DecisionOutcome.PAY
        _decide(db_session, tenant, users, exception, outcome)
    for i in range(22):
        exception = _make_exception(db_session, tenant, customer_account, users, f"82{i:02d}", "100.00", "999.00" if i % 2 == 0 else "888.00")
        outcome = DecisionOutcome.RETURN if i % 2 == 0 else DecisionOutcome.PAY
        _decide(db_session, tenant, users, exception, outcome)

    customer_id = customer.id  # captured before retrain_job's internal rollback expires/detaches this instance
    monkeypatch.setattr(tasks_module, "get_session_factory", lambda: lambda: db_session)

    call_count = {"n": 0}
    real_fit = LogisticRegressionScoringModel.fit

    def flaky_fit(self, X, y):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("simulated training crash")
        return real_fit(self, X, y)

    monkeypatch.setattr(LogisticRegressionScoringModel, "fit", flaky_fit)

    tasks_module.retrain_job()  # must not raise despite the first fit() call failing

    db_session.expire_all()
    failed_rows = [
        m
        for m in db_session.execute(
            select(MlModel).where(MlModel.network_code == "check", MlModel.customer_id.is_(None))
        ).scalars().all()
        if m.status == MlModelStatus.FAILED
    ]
    assert len(failed_rows) == 1
    assert "simulated training crash" in failed_rows[0].metrics_json["error"]

    # the customer's own retrain, later in the same loop iteration, still ran — proof
    # the global failure didn't abort the rest of retrain_job().
    customer_active = get_active_model_row(db_session, "check", customer_id)
    assert customer_active is not None


def test_retrain_cooldown_ignores_the_app_servers_clock(db_session, tenant_factory, monkeypatch):
    """The cooldown is measured on the database's clock, so an app server whose clock runs
    far ahead (here, a day) can't make an immediate retrain look old enough."""
    from datetime import datetime as real_datetime, timedelta

    from openclear.ml import train
    from openclear.ml.train import RetrainCooldownActive, train_model

    tenant, account, users = tenant_factory.make(slug="ml-cooldown-skew", require_dual_control=False)
    for i in range(12):
        exception = _make_exception(db_session, tenant, account, users, f"94{i:02d}", "100.00", "999.00" if i % 2 == 0 else "888.00")
        _decide(db_session, tenant, users, exception, DecisionOutcome.RETURN if i % 2 == 0 else DecisionOutcome.PAY)
    train_model(db_session, "check")
    db_session.commit()

    class _ClockADayAhead(real_datetime):
        @classmethod
        def now(cls, tz=None):
            return real_datetime.now(tz) + timedelta(days=1)

    monkeypatch.setattr(train, "datetime", _ClockADayAhead)
    with pytest.raises(RetrainCooldownActive):
        train_model(db_session, "check")
