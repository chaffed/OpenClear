# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Each bank's own fraud-scoring model (SIMPLIFY_PLAN.md SP-D8, SP-D10): what it trains on,
that nothing is scored until the bank turns scoring on, champion/challenger promotion, and
that a bank can only ever touch its own models — see ml/train.py, ml/predict.py,
ml/registry.py."""

import pytest

from openclear.config import get_settings
from openclear.domain.ml_model import MlModelStatus
from openclear.ml import predict
from openclear.ml.registry import activate_model, create_model_row, get_active_model_row
from openclear.ml.train import train_model
from tests.items import _make_exception, _seed_decisions


@pytest.fixture(autouse=True)
def _no_cooldown(monkeypatch):
    monkeypatch.setattr(get_settings(), "ml_retrain_cooldown_seconds", 0)
    predict.reset_model_cache()
    yield
    predict.reset_model_cache()


def _bank(tenant_factory, db_session, slug, *, scoring=True, decisions=12):
    tenant, account, users = tenant_factory.make(slug=slug)
    tenant.ml_scoring_enabled = scoring
    db_session.commit()
    if decisions:
        _seed_decisions(db_session, tenant, account, users, decisions, prefix=slug[-3:])
    return tenant, account, users


def _row(db_session, *, tenant_id, activate=True, version="v"):
    row = create_model_row(
        db_session, network_code="check", tenant_id=tenant_id, version=version, algorithm="logistic_regression",
        artifact_path="/nonexistent.joblib", trained_from_decision_count=10, metrics_json={}, status=MlModelStatus.TRAINING,
    )
    if activate:
        activate_model(db_session, row.id, expected_tenant_id=tenant_id)
    db_session.commit()
    return row


# --- What a bank's model trains on ---


def test_defaults_applied_at_the_deadline_are_not_training_labels(db_session, tenant_factory):
    """REGULATORY_PLAN.md RG6: an AUTO_DEFAULT decision says what the account's default is, not
    whether the item was fraud, so the model learns only from people's decisions."""
    from sqlalchemy import select

    from openclear.domain.decision import Decision, DecisionSource
    from openclear.domain.exception_item import ExceptionItem

    bank, _a, _u = _bank(tenant_factory, db_session, "lbl-auto", decisions=14)
    rows = db_session.execute(select(Decision).join(ExceptionItem, ExceptionItem.id == Decision.exception_item_id)
                              .where(ExceptionItem.tenant_id == bank.id)).scalars().all()
    for decision in rows[:4]:
        decision.source = DecisionSource.AUTO_DEFAULT
    db_session.commit()

    result = train_model(db_session, "check", tenant_id=bank.id)

    assert result.model_row.trained_from_decision_count == 10


def test_a_bank_model_trains_on_its_own_bank_only(db_session, tenant_factory):
    _bank(tenant_factory, db_session, "own-ccc", decisions=12)
    bank, _a, _u = _bank(tenant_factory, db_session, "own-ddd", decisions=14)

    result = train_model(db_session, "check", tenant_id=bank.id)

    assert result.model_row.trained_from_decision_count == 14
    assert result.model_row.tenant_id == bank.id
    assert get_active_model_row(db_session, "check", tenant_id=bank.id).id == result.model_row.id


def test_tenant_id_is_not_a_model_feature(db_session, tenant_factory):
    from openclear.networks.registry import get_adapter

    tenant, account, users = _bank(tenant_factory, db_session, "feat-xyz", decisions=0)
    exception = _make_exception(db_session, tenant, account, users, "8001", "100.00", "999.00")

    features = get_adapter("check").build_features(db_session, exception)

    assert "tenant_id" not in features
    assert str(tenant.id) not in {str(v) for v in features.values()}


# --- Scoring is off until the bank turns it on ---


def test_scoring_is_off_by_default(db_session, tenant_factory):
    tenant, _account, _users = tenant_factory.make(slug="score-default")

    assert tenant.ml_scoring_enabled is False


def test_nothing_is_scored_while_scoring_is_off(db_session, tenant_factory):
    bank, account, users = _bank(tenant_factory, db_session, "score-off", decisions=12)
    train_model(db_session, "check", tenant_id=bank.id)
    bank.ml_scoring_enabled = False
    db_session.commit()

    exception = _make_exception(db_session, bank, account, users, "8101", "100.00", "999.00")
    assert predict.score_exception(db_session, exception) is None
    assert exception.ml_score is None

    bank.ml_scoring_enabled = True
    db_session.commit()
    exception = _make_exception(db_session, bank, account, users, "8102", "100.00", "999.00")
    assert predict.score_exception(db_session, exception) is not None


def test_a_bank_is_scored_only_by_its_own_model(db_session, tenant_factory):
    bank_a, _a, _u = _bank(tenant_factory, db_session, "prec-aaa", decisions=12)
    bank_b, account_b, users_b = _bank(tenant_factory, db_session, "prec-bbb", decisions=0)
    train_model(db_session, "check", tenant_id=bank_a.id)

    exception = _make_exception(db_session, bank_b, account_b, users_b, "8201", "100.00", "999.00")
    assert predict.score_exception(db_session, exception) is None  # never another bank's model


# --- A bank's models are its own ---


def test_activation_is_confined_to_the_callers_bank(db_session, tenant_factory):
    bank_a, _a, _u = _bank(tenant_factory, db_session, "act-aaa", decisions=0)
    bank_b, _b, _v = _bank(tenant_factory, db_session, "act-bbb", decisions=0)
    a_model = _row(db_session, tenant_id=bank_a.id, activate=False, version="a")

    with pytest.raises(ValueError):
        activate_model(db_session, a_model.id, expected_tenant_id=bank_b.id)

    assert activate_model(db_session, a_model.id, expected_tenant_id=bank_a.id).status == MlModelStatus.ACTIVE


def test_activating_retires_the_banks_previous_model(db_session, tenant_factory):
    bank, _a, _u = _bank(tenant_factory, db_session, "act-ccc", decisions=0)
    first = _row(db_session, tenant_id=bank.id, version="first")
    second = _row(db_session, tenant_id=bank.id, version="second")

    db_session.refresh(first)
    assert first.status == MlModelStatus.RETIRED
    assert get_active_model_row(db_session, "check", tenant_id=bank.id).id == second.id


def test_activating_an_unknown_model_raises(db_session, tenant_factory):
    import uuid

    bank, _a, _u = _bank(tenant_factory, db_session, "act-ddd", decisions=0)
    with pytest.raises(ValueError):
        activate_model(db_session, uuid.uuid4(), expected_tenant_id=bank.id)


# --- Champion / challenger ---


def test_first_bank_model_is_activated_when_none_is_active(db_session, tenant_factory):
    bank, _a, _u = _bank(tenant_factory, db_session, "cc-first", decisions=12)

    result = train_model(db_session, "check", tenant_id=bank.id)

    assert result.promoted
    assert "No model was active" in result.model_row.metrics_json["evaluation"]["reason"]


def test_a_first_model_needs_enough_own_decisions_to_be_activated(db_session, tenant_factory, monkeypatch):
    """REGULATORY_PLAN.md RG6: the floor in sr-11-7.md applies to the first model too."""
    monkeypatch.setattr(get_settings(), "ml_bank_model_min_decisions", 200)
    bank, _a, _u = _bank(tenant_factory, db_session, "cc-first-floor", decisions=12)

    result = train_model(db_session, "check", tenant_id=bank.id)

    assert not result.promoted
    assert "200 are needed before it can be activated" in result.model_row.metrics_json["evaluation"]["reason"]
    assert get_active_model_row(db_session, "check", tenant_id=bank.id) is None


def test_bank_model_needs_enough_own_decisions_to_replace_the_active_one(db_session, tenant_factory, monkeypatch):
    bank, _a, _u = _bank(tenant_factory, db_session, "cc-min", decisions=12)
    first = train_model(db_session, "check", tenant_id=bank.id)
    monkeypatch.setattr(get_settings(), "ml_bank_model_min_decisions", 200)

    second = train_model(db_session, "check", tenant_id=bank.id)

    assert not second.promoted
    assert second.model_row.status == MlModelStatus.RETIRED
    assert "200 are needed" in second.model_row.metrics_json["evaluation"]["reason"]
    assert get_active_model_row(db_session, "check", tenant_id=bank.id).id == first.model_row.id


def test_challenger_is_compared_with_the_active_model_on_the_same_recent_decisions(db_session, tenant_factory, monkeypatch):
    monkeypatch.setattr(get_settings(), "ml_bank_model_min_decisions", 5)
    bank, _a, _u = _bank(tenant_factory, db_session, "cc-same", decisions=12)
    train_model(db_session, "check", tenant_id=bank.id)

    second = train_model(db_session, "check", tenant_id=bank.id)

    evaluation = second.model_row.metrics_json["evaluation"]
    assert "active_model_auc_on_same_holdout" in evaluation
    assert evaluation["holdout_size"] == 3
    assert ("Activated" in evaluation["reason"]) == second.promoted


def test_scheduled_retrain_trains_only_banks_with_scoring_on(db_session, tenant_factory, session_factory, monkeypatch):
    from openclear.workers import tasks

    monkeypatch.setattr(get_settings(), "ml_min_new_decisions_for_retrain", 1)
    monkeypatch.setattr(tasks, "get_session_factory", lambda: session_factory)
    off, _a, _u = _bank(tenant_factory, db_session, "job-off", scoring=False, decisions=12)
    on, _b, _v = _bank(tenant_factory, db_session, "job-onn", decisions=14)

    tasks.retrain_job()

    db_session.expire_all()
    assert get_active_model_row(db_session, "check", tenant_id=off.id) is None
    bank_model = get_active_model_row(db_session, "check", tenant_id=on.id)
    assert bank_model is not None and bank_model.trained_from_decision_count == 14
