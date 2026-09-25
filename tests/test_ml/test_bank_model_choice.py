# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Shared vs. bank-only models (FIX_PLAN.md Phase 5.2): what each model slot trains on,
which model scores an exception, champion/challenger promotion, and slot ownership — see
ml/train.py, ml/predict.py, ml/registry.py."""

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from openclear.config import get_settings
from openclear.db.tenancy import TenantContext
from openclear.domain.customer_ml_setting import MlScoringMode
from openclear.domain.ml_model import MlModelStatus
from openclear.domain.tenant import MlModelSource
from openclear.ml.predict import _resolve_scoring_source
from openclear.ml.registry import activate_model, create_model_row, get_active_model_row
from openclear.ml.train import _load_labeled_decisions, train_model
from openclear.services import customer_ml_service
from tests.test_ml.test_per_customer_ml import _make_customer_with_account, _make_exception, _seed_decisions


@pytest.fixture(autouse=True)
def _no_cooldown(monkeypatch):
    monkeypatch.setattr(get_settings(), "ml_retrain_cooldown_seconds", 0)


def _bank(tenant_factory, db_session, slug, source=MlModelSource.SHARED, decisions=12):
    tenant, account, users = tenant_factory.make(slug=slug)
    tenant.ml_model_source = source
    db_session.commit()
    if decisions:
        _seed_decisions(db_session, tenant, account, users, decisions, prefix=slug[-3:])
    return tenant, account, users


def _row(db_session, *, tenant_id=None, customer_id=None, activate=True, version="v"):
    row = create_model_row(
        db_session, network_code="check", version=version, algorithm="logistic_regression",
        artifact_path="/nonexistent.joblib", trained_from_decision_count=10, metrics_json={},
        status=MlModelStatus.TRAINING, tenant_id=tenant_id, customer_id=customer_id,
    )
    if activate:
        activate_model(db_session, row.id, expected_customer_id=customer_id, expected_tenant_id=None if customer_id else tenant_id)
    db_session.commit()
    return row


# --- Who trains on what ---


def test_a_bank_only_banks_decisions_never_train_the_shared_model(db_session, tenant_factory):
    _bank(tenant_factory, db_session, "shr-aaa", decisions=12)
    _bank(tenant_factory, db_session, "prv-bbb", MlModelSource.PRIVATE, decisions=14)

    result = train_model(db_session, "check")

    assert result.model_row.trained_from_decision_count == 12
    assert result.model_row.tenant_id is None and result.model_row.customer_id is None


def test_a_bank_only_model_trains_on_its_own_bank_only(db_session, tenant_factory):
    _bank(tenant_factory, db_session, "shr-ccc", decisions=12)
    private, _a, _u = _bank(tenant_factory, db_session, "prv-ddd", MlModelSource.PRIVATE, decisions=14)

    result = train_model(db_session, "check", tenant_id=private.id)

    assert result.model_row.trained_from_decision_count == 14
    assert result.model_row.tenant_id == private.id and result.model_row.customer_id is None
    assert get_active_model_row(db_session, "check", tenant_id=private.id).id == result.model_row.id
    assert get_active_model_row(db_session, "check") is None  # the shared slot is untouched


def _submit_fraud_example(db_session, tenant, account, users, check_number):
    from openclear.services.fraud_training_service import CheckFraudRawInput, submit_check_fraud_example

    ctx = TenantContext(
        tenant_id=tenant.id, user_id=users["admin"].id, security_group_id=uuid.uuid4(), permissions=frozenset(),
        tenant_slug=tenant.slug, tenant_name=tenant.name, accent_color=None, has_logo=False, has_favicon=False,
        customer_id=None, customer_name=None,
    )
    example = submit_check_fraud_example(
        db_session, tenant.id, ctx,
        new_item=CheckFraudRawInput(account_id=account.id, check_number=check_number,
                                    presented_amount=Decimal("500.00"), presented_date=date(2026, 1, 15)),
        reason_code="known fraud",
    )
    db_session.commit()
    return example


def test_fraud_examples_reach_the_shared_model_only_once_approved(db_session, tenant_factory):
    tenant, account, users = _bank(tenant_factory, db_session, "shr-fex", decisions=10)
    example = _submit_fraud_example(db_session, tenant, account, users, "7001")

    assert len(_load_labeled_decisions(db_session, "check")) == 10

    example.shared_training_approved_at = datetime.now(timezone.utc)
    db_session.commit()
    assert len(_load_labeled_decisions(db_session, "check")) == 11


def test_a_bank_only_banks_fraud_examples_train_its_own_model_without_approval(db_session, tenant_factory):
    tenant, account, users = _bank(tenant_factory, db_session, "prv-fex", MlModelSource.PRIVATE, decisions=10)
    _submit_fraud_example(db_session, tenant, account, users, "7002")

    assert len(_load_labeled_decisions(db_session, "check", tenant_id=tenant.id)) == 11
    assert len(_load_labeled_decisions(db_session, "check")) == 0


def test_tenant_id_is_no_longer_a_model_feature(db_session, tenant_factory):
    from openclear.networks.registry import get_adapter

    tenant, account, users = _bank(tenant_factory, db_session, "feat-xyz", decisions=0)
    exception = _make_exception(db_session, tenant, account, users, "8001", "100.00", "999.00")

    features = get_adapter("check").build_features(db_session, exception)

    assert "tenant_id" not in features
    assert str(tenant.id) not in {str(v) for v in features.values()}


# --- Which model scores an exception ---


def test_scoring_precedence(db_session, tenant_factory):
    shared_bank, _a, _u = _bank(tenant_factory, db_session, "prec-shr", decisions=0)
    private_bank, _a2, _u2 = _bank(tenant_factory, db_session, "prec-prv", MlModelSource.PRIVATE, decisions=0)
    customer, _acct = _make_customer_with_account(db_session, shared_bank, "PC-1")
    private_customer, _acct2 = _make_customer_with_account(db_session, private_bank, "PC-2")

    def source(tenant, customer_id=None):
        row = _resolve_scoring_source(db_session, tenant.id, "check", customer_id)
        return row.id if row else None

    # Nothing trained yet: no score anywhere.
    assert source(shared_bank) is None and source(private_bank) is None

    shared = _row(db_session, version="shared")
    assert source(shared_bank) == shared.id
    assert source(private_bank) is None  # a bank-only bank never falls back to the shared model

    bank_only = _row(db_session, tenant_id=private_bank.id, version="bank")
    assert source(private_bank) == bank_only.id
    assert source(shared_bank) == shared.id

    # Customers: AUTO falls back to the bank's model, then uses their own once active.
    assert source(shared_bank, customer.id) == shared.id
    assert source(private_bank, private_customer.id) == bank_only.id
    own = _row(db_session, tenant_id=shared_bank.id, customer_id=customer.id, version="cust")
    assert source(shared_bank, customer.id) == own.id

    customer_ml_service.set_mode(db_session, shared_bank.id, customer.id, "check", MlScoringMode.GLOBAL)
    db_session.commit()
    assert source(shared_bank, customer.id) == shared.id  # "Bank's model" ignores the customer's own

    customer_ml_service.set_mode(db_session, private_bank.id, private_customer.id, "check", MlScoringMode.CUSTOMER)
    db_session.commit()
    assert source(private_bank, private_customer.id) == bank_only.id  # no own model yet → the bank's


# --- Slot ownership ---


def test_activation_is_confined_to_the_callers_slot(db_session, tenant_factory):
    bank_a, _a, _u = _bank(tenant_factory, db_session, "own-aaa", MlModelSource.PRIVATE, decisions=0)
    bank_b, _b, _v = _bank(tenant_factory, db_session, "own-bbb", MlModelSource.PRIVATE, decisions=0)
    shared = _row(db_session, activate=False, version="shared")
    a_model = _row(db_session, tenant_id=bank_a.id, activate=False, version="a")

    with pytest.raises(ValueError):
        activate_model(db_session, shared.id, expected_customer_id=None, expected_tenant_id=bank_a.id)  # a bank can't touch shared
    with pytest.raises(ValueError):
        activate_model(db_session, a_model.id, expected_customer_id=None, expected_tenant_id=bank_b.id)  # nor another bank's
    with pytest.raises(ValueError):
        activate_model(db_session, a_model.id, expected_customer_id=None)  # nor can the shared slot adopt a bank model

    assert activate_model(db_session, a_model.id, expected_customer_id=None, expected_tenant_id=bank_a.id).status == MlModelStatus.ACTIVE


# --- Champion / challenger ---


def test_first_bank_model_is_activated_when_none_is_active(db_session, tenant_factory):
    bank, _a, _u = _bank(tenant_factory, db_session, "cc-first", MlModelSource.PRIVATE, decisions=12)

    result = train_model(db_session, "check", tenant_id=bank.id)

    assert result.promoted
    assert "No model was active" in result.model_row.metrics_json["evaluation"]["reason"]


def test_bank_model_needs_enough_own_decisions_to_replace_the_active_one(db_session, tenant_factory):
    bank, _a, _u = _bank(tenant_factory, db_session, "cc-min", MlModelSource.PRIVATE, decisions=12)
    first = train_model(db_session, "check", tenant_id=bank.id)

    second = train_model(db_session, "check", tenant_id=bank.id)

    assert not second.promoted
    assert second.model_row.status == MlModelStatus.RETIRED
    assert "200 are needed" in second.model_row.metrics_json["evaluation"]["reason"]
    assert get_active_model_row(db_session, "check", tenant_id=bank.id).id == first.model_row.id


def test_challenger_is_compared_with_the_active_model_on_the_same_recent_decisions(db_session, tenant_factory, monkeypatch):
    monkeypatch.setattr(get_settings(), "ml_bank_model_min_decisions", 5)
    bank, _a, _u = _bank(tenant_factory, db_session, "cc-same", MlModelSource.PRIVATE, decisions=12)
    train_model(db_session, "check", tenant_id=bank.id)

    second = train_model(db_session, "check", tenant_id=bank.id)

    evaluation = second.model_row.metrics_json["evaluation"]
    assert "active_model_auc_on_same_holdout" in evaluation
    assert evaluation["holdout_size"] == 3
    assert ("Activated" in evaluation["reason"]) == second.promoted


def test_scheduled_retrain_trains_each_bank_only_bank_separately(db_session, tenant_factory, session_factory, monkeypatch):
    from openclear.workers import tasks

    monkeypatch.setattr(get_settings(), "ml_min_new_decisions_for_retrain", 1)
    monkeypatch.setattr(tasks, "get_session_factory", lambda: session_factory)
    _bank(tenant_factory, db_session, "job-shr", decisions=12)
    private, _a, _u = _bank(tenant_factory, db_session, "job-prv", MlModelSource.PRIVATE, decisions=14)

    tasks.retrain_job()

    db_session.expire_all()
    shared = get_active_model_row(db_session, "check")
    bank_model = get_active_model_row(db_session, "check", tenant_id=private.id)
    assert shared is not None and shared.trained_from_decision_count == 12
    assert bank_model is not None and bank_model.trained_from_decision_count == 14
