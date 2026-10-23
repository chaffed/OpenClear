# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import uuid
from datetime import date
from decimal import Decimal

from openclear.db.tenancy import TenantContext
from openclear.domain.decision import DecisionOutcome
from openclear.networks.check.ingestion import PaidItemSubmission, ingest_paid_item
from openclear.services import decision_service, issued_item_service
from tests.conftest import login_headers


def _scoring_on(db_session, tenant):
    """Bank admins retrain and activate their own model, once the bank has turned fraud
    scoring on (SIMPLIFY_PLAN.md SP-D10)."""
    tenant.ml_scoring_enabled = True
    db_session.commit()


def _seed_labeled_decisions(db_session, tenant, account, users, count: int = 12) -> None:
    for i in range(count):
        issued_item_service.create_issued_item(
            db_session,
            tenant.id,
            issued_item_service.IssuedItemInput(
                account_id=account.id,
                check_number=f"70{i:02d}",
                amount=Decimal("100.00"),
                payee_name="Vendor",
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
                check_number=f"70{i:02d}",
                presented_amount=Decimal("999.00" if i % 2 == 0 else "888.00"),
                presented_date=date(2026, 1, 10),
            ),
        )
        db_session.commit()

        from openclear.repositories.exception_repo import ExceptionRepository

        exception = ExceptionRepository(db_session, tenant.id).list(source_item_id=paid_item.id)[0]
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
        outcome = DecisionOutcome.RETURN if i % 2 == 0 else DecisionOutcome.PAY
        decision_service.decide(db_session, tenant.id, exception.id, ctx, outcome=outcome, reason_code="test", notes=None)
        db_session.commit()


def test_retrain_endpoint_trains_and_promotes_model(client, db_session, tenant_factory, monkeypatch):
    from openclear.config import get_settings

    monkeypatch.setattr(get_settings(), "ml_bank_model_min_decisions", 10)  # the floor itself: tests/test_ml
    tenant, account, users = tenant_factory.make(slug="admin-ml-retrain")
    _scoring_on(db_session, tenant)
    _seed_labeled_decisions(db_session, tenant, account, users)

    headers = login_headers(client, tenant.slug, users["admin"].email)
    resp = client.post("/api/v1/admin/ml/retrain", headers=headers, params={"network_code": "check"})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["network_code"] == "check"
    assert body["promoted"] is True
    assert body["model"]["status"] == "active"


def test_retrain_endpoint_rejects_insufficient_data(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="admin-ml-insufficient")
    _scoring_on(db_session, tenant)
    headers = login_headers(client, tenant.slug, users["admin"].email)

    resp = client.post("/api/v1/admin/ml/retrain", headers=headers, params={"network_code": "check"})

    assert resp.status_code == 409


def test_list_and_activate_ml_models(client, db_session, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="admin-ml-list")
    _scoring_on(db_session, tenant)
    _seed_labeled_decisions(db_session, tenant, account, users)
    headers = login_headers(client, tenant.slug, users["admin"].email)

    client.post("/api/v1/admin/ml/retrain", headers=headers, params={"network_code": "check"})

    models = client.get("/api/v1/admin/ml/models", headers=headers, params={"network_code": "check"}).json()
    assert len(models) >= 1
    model_id = models[0]["id"]

    activated = client.patch(f"/api/v1/admin/ml/models/{model_id}/activate", headers=headers)
    assert activated.status_code == 200
    assert activated.json()["status"] == "active"


def test_non_admin_cannot_trigger_retrain(client, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="admin-ml-forbidden")
    headers = login_headers(client, tenant.slug, users["preparer"].email)

    resp = client.post("/api/v1/admin/ml/retrain", headers=headers, params={"network_code": "check"})

    assert resp.status_code == 403


def test_immediate_reretrain_is_rejected_by_cooldown(client, db_session, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="admin-ml-cooldown")
    _scoring_on(db_session, tenant)
    _seed_labeled_decisions(db_session, tenant, account, users)
    headers = login_headers(client, tenant.slug, users["admin"].email)

    first = client.post("/api/v1/admin/ml/retrain", headers=headers, params={"network_code": "check"})
    assert first.status_code == 200

    second = client.post("/api/v1/admin/ml/retrain", headers=headers, params={"network_code": "check"})
    assert second.status_code == 409


def test_retrain_is_refused_while_scoring_is_off(client, db_session, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="admin-ml-off-409")
    _seed_labeled_decisions(db_session, tenant, account, users)
    headers = login_headers(client, tenant.slug, users["admin"].email)

    retrain = client.post("/api/v1/admin/ml/retrain", headers=headers, params={"network_code": "check"})

    assert retrain.status_code == 409
    assert "Fraud scoring is off" in retrain.json()["detail"]


def test_model_list_shows_only_this_banks_models(client, db_session, tenant_factory):
    from openclear.domain.ml_model import MlModelStatus
    from openclear.ml.registry import create_model_row

    tenant, _account, users = tenant_factory.make(slug="admin-ml-list-mine")
    other, _other_account, _other_users = tenant_factory.make(slug="admin-ml-list-other")
    common = dict(network_code="check", algorithm="logistic_regression", artifact_path="/tmp/x.joblib",
                  trained_from_decision_count=10, metrics_json={}, status=MlModelStatus.RETIRED)
    mine = create_model_row(db_session, version="mine", tenant_id=tenant.id, **common)
    create_model_row(db_session, version="theirs", tenant_id=other.id, **common)
    db_session.commit()
    headers = login_headers(client, tenant.slug, users["admin"].email)

    models = client.get("/api/v1/admin/ml/models", headers=headers).json()

    assert [m["id"] for m in models] == [str(mine.id)]


def test_bank_cannot_activate_another_banks_model(client, db_session, tenant_factory):
    from openclear.domain.ml_model import MlModelStatus
    from openclear.ml.registry import create_model_row

    tenant, _account, users = tenant_factory.make(slug="admin-ml-activate-other")
    _scoring_on(db_session, tenant)
    other, _a, _u = tenant_factory.make(slug="admin-ml-activate-other-2")
    theirs = create_model_row(
        db_session, network_code="check", version="theirs", algorithm="logistic_regression", artifact_path="/tmp/x.joblib",
        trained_from_decision_count=10, metrics_json={}, status=MlModelStatus.RETIRED, tenant_id=other.id,
    )
    db_session.commit()
    headers = login_headers(client, tenant.slug, users["admin"].email)

    assert client.patch(f"/api/v1/admin/ml/models/{theirs.id}/activate", headers=headers).status_code == 404


def test_the_shared_model_summary_is_gone(client, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="admin-ml-summary")
    headers = login_headers(client, tenant.slug, users["admin"].email)

    assert client.get("/api/v1/admin/ml/shared-model", headers=headers).status_code == 404
