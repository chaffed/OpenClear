# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Training at scale (SCALE_PLAN.md S5): a training window and row cap, what a run
records about itself, the scheduled retrain's notion of "new" decisions under a cap, and
the bounded model cache."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from openclear.config import get_settings
from openclear.domain.decision import Decision
from openclear.domain.exception_item import ExceptionItem
from openclear.domain.tenant import MlModelSource
from openclear.ml import predict
from openclear.ml.train import count_labeled_decisions, train_model
from openclear.workers import tasks
from tests.test_ml.test_bank_model_choice import _bank, _submit_fraud_example


@pytest.fixture(autouse=True)
def _no_cooldown(monkeypatch):
    monkeypatch.setattr(get_settings(), "ml_retrain_cooldown_seconds", 0)


def _age_decisions(db_session, tenant, count: int, days: int) -> None:
    """Backdates the oldest `count` of the bank's decisions by `days`."""
    decisions = db_session.execute(
        select(Decision).join(ExceptionItem, Decision.exception_item_id == ExceptionItem.id)
        .where(ExceptionItem.tenant_id == tenant.id).order_by(Decision.decided_at)
    ).scalars().all()
    for decision in decisions[:count]:
        decision.decided_at = decision.decided_at - timedelta(days=days)
    db_session.commit()


def test_training_reads_only_the_window_but_keeps_fraud_examples(db_session, tenant_factory, monkeypatch):
    monkeypatch.setattr(get_settings(), "ml_training_window_days", 30)
    tenant, account, users = _bank(tenant_factory, db_session, "win-aaa", MlModelSource.PRIVATE, decisions=16)
    _age_decisions(db_session, tenant, 4, days=400)
    example = _submit_fraud_example(db_session, tenant, account, users, "7101")
    example_decision = db_session.execute(select(Decision).where(Decision.exception_item_id == example.id)).scalar_one()
    example_decision.decided_at = datetime.now(timezone.utc) - timedelta(days=900)  # curated: kept however old
    db_session.commit()

    result = train_model(db_session, "check", tenant_id=tenant.id)

    training = result.model_row.metrics_json["training"]
    assert result.model_row.trained_from_decision_count == 13  # 12 recent decisions plus the fraud example
    assert (training["rows"], training["eligible_decisions"], training["capped"]) == (13, 17, True)
    assert training["window_days"] == 30 and training["seconds"] >= 0


def test_the_row_cap_keeps_the_newest_rows(db_session, tenant_factory, monkeypatch):
    monkeypatch.setattr(get_settings(), "ml_training_max_rows", 12)
    tenant, _account, _users = _bank(tenant_factory, db_session, "cap-bbb", MlModelSource.PRIVATE, decisions=20)

    result = train_model(db_session, "check", tenant_id=tenant.id)

    training = result.model_row.metrics_json["training"]
    assert (training["rows"], training["eligible_decisions"], training["capped"]) == (12, 20, True)


def test_a_capped_run_does_not_look_due_again(db_session, tenant_factory, monkeypatch):
    """The scheduled retrain counts new decisions against everything eligible at the last
    run, not the rows the cap let it use; otherwise a capped slot would retrain nightly."""
    monkeypatch.setattr(get_settings(), "ml_training_max_rows", 12)
    tenant, _account, _users = _bank(tenant_factory, db_session, "cap-ccc", MlModelSource.PRIVATE, decisions=20)
    train_model(db_session, "check", tenant_id=tenant.id)

    trained_on = tasks._most_recently_trained_count(db_session, "check", tenant_id=tenant.id)
    assert trained_on == 20
    assert count_labeled_decisions(db_session, "check", tenant_id=tenant.id) - trained_on == 0


def test_the_model_cache_is_bounded(db_session, tenant_factory, monkeypatch):
    monkeypatch.setattr(get_settings(), "ml_model_cache_size", 2)
    predict.reset_model_cache()
    banks = []
    for slug in ("lru-aaa", "lru-bbb", "lru-ccc"):
        tenant, _account, _users = _bank(tenant_factory, db_session, slug, MlModelSource.PRIVATE, decisions=12)
        train_model(db_session, "check", tenant_id=tenant.id)
        banks.append(tenant)

    for tenant in banks:
        assert predict._load_model(db_session, tenant.id, "check", None) is not None
    assert [key[1] for key in predict._MODEL_CACHE] == [banks[1].id, banks[2].id]  # the first was dropped

    predict._load_model(db_session, banks[1].id, "check", None)  # a hit makes it the most recent
    predict._load_model(db_session, banks[0].id, "check", None)
    assert [key[1] for key in predict._MODEL_CACHE] == [banks[1].id, banks[0].id]
    predict.reset_model_cache()
