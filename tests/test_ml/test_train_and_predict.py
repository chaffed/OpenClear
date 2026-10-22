# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import pytest

from openclear.domain.decision import DecisionOutcome
from tests.items import _decide, _make_exception


def test_train_model_raises_below_minimum_decision_count(db_session, tenant_factory):
    from openclear.ml.train import InsufficientTrainingData, train_model

    tenant, _account, _users = tenant_factory.make(slug="ml-insufficient")

    with pytest.raises(InsufficientTrainingData):
        train_model(db_session, "check", tenant_id=tenant.id)


def test_score_exception_returns_none_before_any_model_trained(db_session, tenant_factory):
    from openclear.ml.predict import reset_model_cache, score_exception

    reset_model_cache()
    tenant, account, users = tenant_factory.make(slug="ml-cold-start")
    tenant.ml_scoring_enabled = True
    exception = _make_exception(db_session, tenant, account, users, "9101", "100.00", "999.00")

    score = score_exception(db_session, exception)

    assert score is None
    assert exception.ml_score is None


def test_train_then_score_populates_ml_score_on_new_exceptions(db_session, tenant_factory):
    from openclear.ml.predict import reset_model_cache, score_exception
    from openclear.ml.train import train_model

    reset_model_cache()
    tenant, account, users = tenant_factory.make(slug="ml-train-score", require_dual_control=False)
    tenant.ml_scoring_enabled = True

    # Generate 12 labeled decisions (above MIN_DECISIONS_TO_TRAIN=10), alternating
    # outcomes so both classes are present for a meaningful holdout split.
    for i in range(12):
        exception = _make_exception(
            db_session, tenant, account, users, f"92{i:02d}", "100.00", "999.00" if i % 2 == 0 else "888.00"
        )
        outcome = DecisionOutcome.RETURN if i % 2 == 0 else DecisionOutcome.PAY
        result = _decide(db_session, tenant, users, exception, outcome)
        assert result.error is None

    train_result = train_model(db_session, "check", tenant_id=tenant.id)
    assert train_result.promoted is True
    assert train_result.model_row.trained_from_decision_count == 12

    new_exception = _make_exception(db_session, tenant, account, users, "9301", "100.00", "999.00")
    score = score_exception(db_session, new_exception)

    assert score is not None
    assert 0.0 <= score <= 1.0
    assert new_exception.ml_model_version == train_result.model_row.version


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

    train_model(db_session, "check", tenant_id=tenant.id)

    with pytest.raises(RetrainCooldownActive):
        train_model(db_session, "check", tenant_id=tenant.id)


def test_retrain_cooldown_lifts_after_the_window_elapses(db_session, tenant_factory, monkeypatch):
    from openclear.config import get_settings
    from openclear.ml.train import train_model

    monkeypatch.setattr(get_settings(), "ml_retrain_cooldown_seconds", 0)
    tenant, account, users = tenant_factory.make(slug="ml-cooldown-lifted", require_dual_control=False)
    for i in range(12):
        exception = _make_exception(db_session, tenant, account, users, f"94{i:02d}", "100.00", "999.00" if i % 2 == 0 else "888.00")
        outcome = DecisionOutcome.RETURN if i % 2 == 0 else DecisionOutcome.PAY
        _decide(db_session, tenant, users, exception, outcome)

    train_model(db_session, "check", tenant_id=tenant.id)
    second = train_model(db_session, "check", tenant_id=tenant.id)  # cooldown is 0s, so this must succeed, not raise
    assert second.model_row.version != None


def test_retrain_job_continues_and_records_failure_after_unexpected_error(db_session, tenant_factory, session_factory, monkeypatch):
    """One bank's failed retrain is recorded as a FAILED model row and doesn't stop the next
    bank's."""
    import openclear.workers.tasks as tasks_module
    from sqlalchemy import select

    from openclear.domain.ml_model import MlModel, MlModelStatus
    from openclear.ml.model import LogisticRegressionScoringModel
    from openclear.ml.registry import get_active_model_row

    banks = []
    for slug in ("ml-resilience-a", "ml-resilience-b"):
        tenant, account, users = tenant_factory.make(slug=slug)
        tenant.ml_scoring_enabled = True
        db_session.commit()
        # retrain_job()'s own gate (ml_min_new_decisions_for_retrain, default 20) is stricter
        # than train_model's minimum (10).
        for i in range(22):
            exception = _make_exception(db_session, tenant, account, users, f"81{i:02d}", "100.00", "999.00" if i % 2 == 0 else "888.00")
            _decide(db_session, tenant, users, exception, DecisionOutcome.RETURN if i % 2 == 0 else DecisionOutcome.PAY)
        banks.append(tenant.id)

    monkeypatch.setattr(tasks_module, "get_session_factory", lambda: session_factory)
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
    failed = db_session.execute(select(MlModel).where(MlModel.tenant_id.in_(banks), MlModel.status == MlModelStatus.FAILED)).scalars().all()
    assert len(failed) == 1
    assert "simulated training crash" in failed[0].metrics_json["error"]
    other_bank = next(b for b in banks if b != failed[0].tenant_id)
    assert get_active_model_row(db_session, "check", tenant_id=other_bank) is not None


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
    train_model(db_session, "check", tenant_id=tenant.id)
    db_session.commit()

    class _ClockADayAhead(real_datetime):
        @classmethod
        def now(cls, tz=None):
            return real_datetime.now(tz) + timedelta(days=1)

    monkeypatch.setattr(train, "datetime", _ClockADayAhead)
    with pytest.raises(RetrainCooldownActive):
        train_model(db_session, "check", tenant_id=tenant.id)
