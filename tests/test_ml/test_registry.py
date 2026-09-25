# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import pytest

from openclear.domain.ml_model import MlModelStatus
from openclear.ml.registry import activate_model, create_model_row
from openclear.services import customer_service


def _make_row(db_session, *, customer_id=None, status=MlModelStatus.TRAINING):
    return create_model_row(
        db_session,
        network_code="check",
        version="v1",
        algorithm="logistic_regression",
        artifact_path="/tmp/fake.joblib",
        trained_from_decision_count=10,
        metrics_json={"auc": 0.9},
        status=status,
        customer_id=customer_id,
    )


def test_activate_model_succeeds_when_scope_matches(db_session, tenant_factory):
    tenant_factory.make(slug="ml-registry-match")
    row = _make_row(db_session, customer_id=None)
    db_session.commit()

    activated = activate_model(db_session, row.id, expected_customer_id=None)
    assert activated.status == MlModelStatus.ACTIVE


def test_activate_model_rejects_customer_model_via_bank_wide_scope(db_session, tenant_factory):
    tenant, _account, _users = tenant_factory.make(slug="ml-registry-cust-via-global")
    customer = customer_service.create_customer(db_session, tenant.id, customer_service.CustomerInput(customer_number="C-1", name="Acme"))
    row = _make_row(db_session, customer_id=customer.id)
    db_session.commit()

    with pytest.raises(ValueError):
        activate_model(db_session, row.id, expected_customer_id=None)


def test_activate_model_rejects_global_model_via_customer_scope(db_session, tenant_factory):
    tenant, _account, _users = tenant_factory.make(slug="ml-registry-global-via-cust")
    customer = customer_service.create_customer(db_session, tenant.id, customer_service.CustomerInput(customer_number="C-1", name="Acme"))
    row = _make_row(db_session, customer_id=None)
    db_session.commit()

    with pytest.raises(ValueError):
        activate_model(db_session, row.id, expected_customer_id=customer.id)


def test_activate_model_rejects_another_customers_model(db_session, tenant_factory):
    tenant, _account, _users = tenant_factory.make(slug="ml-registry-cust-vs-cust")
    customer_a = customer_service.create_customer(db_session, tenant.id, customer_service.CustomerInput(customer_number="C-A", name="A Co"))
    customer_b = customer_service.create_customer(db_session, tenant.id, customer_service.CustomerInput(customer_number="C-B", name="B Co"))
    row_a = _make_row(db_session, customer_id=customer_a.id)
    db_session.commit()

    with pytest.raises(ValueError):
        activate_model(db_session, row_a.id, expected_customer_id=customer_b.id)

    # but activating it against its own scope still works
    activated = activate_model(db_session, row_a.id, expected_customer_id=customer_a.id)
    assert activated.status == MlModelStatus.ACTIVE


def test_activate_model_unknown_id_raises(db_session, tenant_factory):
    import uuid

    tenant_factory.make(slug="ml-registry-unknown")
    with pytest.raises(ValueError):
        activate_model(db_session, uuid.uuid4(), expected_customer_id=None)
