# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""The platform operator's shared-model API (api/v1/platform_ml.py) and platform key
scopes (FIX_PLAN.md Phase 5)."""

import pytest

from openclear.config import get_settings
from openclear.domain.audit_log_entry import AuditLogEntry
from openclear.domain.ml_model import MlModelStatus
from openclear.domain.tenant import MlModelSource
from openclear.ml.registry import create_model_row
from openclear.services import platform_api_key_service, provisioning_service
from tests.test_api.test_admin_ml import _seed_labeled_decisions
from tests.test_ml.test_bank_model_choice import _submit_fraud_example


@pytest.fixture(autouse=True)
def _no_cooldown(monkeypatch):
    monkeypatch.setattr(get_settings(), "ml_retrain_cooldown_seconds", 0)


def _key(db_session, *scopes):
    _row, raw = platform_api_key_service.generate_and_create(db_session, "test key", scopes)
    db_session.commit()
    return {"X-Api-Key": raw}


def test_platform_ml_needs_the_shared_model_scope(client, db_session):
    usage_key = _key(db_session, "usage")
    operator_key = _key(db_session, "shared_model")

    assert client.get("/api/v1/platform/ml/models", headers={"X-Api-Key": "pp_nope"}).status_code == 401
    assert client.get("/api/v1/platform/ml/models", headers=usage_key).status_code == 403
    assert client.get("/api/v1/platform/ml/models", headers=operator_key).status_code == 200
    # ...and scopes cut both ways: the operator key can't read usage metering.
    period = {"period_start": "2026-01-01", "period_end": "2026-01-31"}
    assert client.get("/api/v1/platform/usage", headers=operator_key, params=period).status_code == 403
    assert client.get("/api/v1/platform/usage", headers=usage_key, params=period).status_code == 200


def test_unknown_scopes_are_refused(db_session):
    with pytest.raises(ValueError):
        platform_api_key_service.generate_and_create(db_session, "bad", ("everything",))


def test_operator_retrains_activates_and_inspects_the_shared_model(client, db_session, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="plat-ml-train")
    _seed_labeled_decisions(db_session, tenant, account, users)
    operator = _key(db_session, "shared_model")

    trained = client.post("/api/v1/platform/ml/retrain", headers=operator, params={"network_code": "check"})
    assert trained.status_code == 200, trained.text
    model_id = trained.json()["model"]["id"]

    listed = client.get("/api/v1/platform/ml/models", headers=operator, params={"network_code": "check"}).json()
    assert [m["id"] for m in listed] == [model_id]

    importance = client.get(f"/api/v1/platform/ml/models/{model_id}/feature-importance", headers=operator).json()
    assert importance and "tenant_id" not in importance

    assert client.patch(f"/api/v1/platform/ml/models/{model_id}/activate", headers=operator).json()["status"] == "active"


def test_operator_cannot_touch_a_banks_own_model(client, db_session, tenant_factory):
    tenant, _account, _users = tenant_factory.make(slug="plat-ml-bank-404")
    bank_model = create_model_row(
        db_session, network_code="check", version="bank", algorithm="logistic_regression", artifact_path="/tmp/x.joblib",
        trained_from_decision_count=10, metrics_json={}, status=MlModelStatus.RETIRED, tenant_id=tenant.id,
    )
    db_session.commit()
    operator = _key(db_session, "shared_model")

    assert client.patch(f"/api/v1/platform/ml/models/{bank_model.id}/activate", headers=operator).status_code == 404
    assert client.get(f"/api/v1/platform/ml/models/{bank_model.id}/feature-importance", headers=operator).status_code == 404


def test_fraud_examples_are_reviewed_before_training_the_shared_model(client, db_session, tenant_factory):
    shared_bank, account, users = tenant_factory.make(slug="plat-fex-shr")
    private_bank, private_account, private_users = tenant_factory.make(slug="plat-fex-prv")
    private_bank.ml_model_source = MlModelSource.PRIVATE
    db_session.commit()
    example = _submit_fraud_example(db_session, shared_bank, account, users, "6001")
    _submit_fraud_example(db_session, private_bank, private_account, private_users, "6002")
    operator = _key(db_session, "shared_model")

    pending = client.get("/api/v1/platform/ml/fraud-examples/pending", headers=operator).json()
    assert [p["exception_id"] for p in pending] == [str(example.id)]  # bank-only examples never need review
    assert pending[0]["tenant_slug"] == "plat-fex-shr"

    approved = client.post(f"/api/v1/platform/ml/fraud-examples/{example.id}/approve", headers=operator)
    assert approved.status_code == 204

    assert client.get("/api/v1/platform/ml/fraud-examples/pending", headers=operator).json() == []
    db_session.expire_all()
    assert example.shared_training_approved_at is not None
    audit = db_session.query(AuditLogEntry).filter_by(tenant_id=shared_bank.id, action="fraud_example.approve_for_shared_model").one()
    assert audit.actor_user_id is None


def test_operator_can_lift_a_new_banks_switch_lock(client, db_session):
    identity = provisioning_service.create_tenant_with_admin(
        db_session, tenant_name="Locked Bank", tenant_slug="plat-lock", admin_email="a@lock.example.com",
        admin_password="Long-enough-pw-1",
    )
    db_session.commit()
    operator = _key(db_session, "shared_model")

    resp = client.post(f"/api/v1/platform/tenants/{identity.tenant.id}/ml-switch-lock/clear", headers=operator)

    assert resp.status_code == 200
    db_session.expire_all()
    assert identity.tenant.ml_private_switch_allowed_at is None
    assert db_session.query(AuditLogEntry).filter_by(tenant_id=identity.tenant.id, action="tenant.ml_switch_lock_cleared").count() == 1
