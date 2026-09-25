# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

from datetime import date
from decimal import Decimal

from openclear.db.tenancy import TenantContext
from openclear.domain.decision import DecisionOutcome
from openclear.domain.ml_model import MlModelStatus
from openclear.ml.registry import create_model_row
from openclear.networks.check.ingestion import PaidItemSubmission, ingest_paid_item
from openclear.repositories.exception_repo import ExceptionRepository
from openclear.services import account_service, customer_service, decision_service, issued_item_service, security_group_service, user_service
from tests.conftest import TenantFactory


def _login(client, tenant_slug, email, password=TenantFactory.PASSWORD):
    client.get("/ui/login")
    csrf = client.cookies.get("csrf_token")
    return client.post(
        "/ui/login", data={"tenant_slug": tenant_slug, "email": email, "password": password, "csrf_token": csrf, "next": "/ui/"}
    )


def _make_customer(db_session, tenant, customer_number="C-1"):
    customer = customer_service.create_customer(
        db_session, tenant.id, customer_service.CustomerInput(customer_number=customer_number, name="Acme Co")
    )
    account = account_service.create_account(
        db_session, tenant.id, account_service.AccountInput(account_number=f"{customer_number}-ACCT", name="Acct", customer_id=customer.id)
    )
    db_session.commit()
    return customer, account


def _seed_labeled_decisions(db_session, tenant, account, users, count=12):
    import uuid

    for i in range(count):
        issued_item_service.create_issued_item(
            db_session,
            tenant.id,
            issued_item_service.IssuedItemInput(
                account_id=account.id, check_number=f"w{i:02d}", amount=Decimal("100.00"), payee_name="Vendor", issue_date=date(2026, 1, 1)
            ),
            submitted_by_user_id=users["preparer"].id,
        )
        db_session.commit()
        paid_item = ingest_paid_item(
            db_session,
            tenant.id,
            PaidItemSubmission(
                account_id=account.id, check_number=f"w{i:02d}", presented_amount=Decimal("999.00" if i % 2 == 0 else "888.00"), presented_date=date(2026, 1, 10)
            ),
        )
        db_session.commit()
        exception = ExceptionRepository(db_session, tenant.id).list(source_item_id=paid_item.id)[0]
        ctx = TenantContext(
            tenant_id=tenant.id, user_id=users["approver"].id, security_group_id=uuid.uuid4(), permissions=frozenset(),
            tenant_slug=tenant.slug, tenant_name=tenant.name, accent_color=None, has_logo=False, has_favicon=False,
            customer_id=None, customer_name=None,
        )
        outcome = DecisionOutcome.RETURN if i % 2 == 0 else DecisionOutcome.PAY
        decision_service.decide(db_session, tenant.id, exception.id, ctx, outcome=outcome, reason_code="test", notes=None)
        db_session.commit()


def _csrf(client):
    return client.cookies.get("csrf_token")


def test_customer_ml_page_requires_admin_permission(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="web-cust-ml-forbidden")
    customer, _account2 = _make_customer(db_session, tenant)
    _login(client, tenant.slug, users["preparer"].email)

    resp = client.get(f"/ui/admin/customers/{customer.id}/ml", follow_redirects=False)
    assert resp.status_code == 403


def test_customer_ml_page_shows_networks(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="web-cust-ml-show")
    customer, _account2 = _make_customer(db_session, tenant)
    _login(client, tenant.slug, users["admin"].email)

    resp = client.get(f"/ui/admin/customers/{customer.id}/ml")
    assert resp.status_code == 200
    assert "check" in resp.text
    assert "ach" in resp.text


def test_customer_ml_page_404s_for_customer_in_another_tenant(client, db_session, tenant_factory):
    tenant_a, _account_a, users_a = tenant_factory.make(slug="web-cust-ml-xtenant-a")
    tenant_b, _account_b, _users_b = tenant_factory.make(slug="web-cust-ml-xtenant-b")
    customer_b, _account_b2 = _make_customer(db_session, tenant_b, "C-B")
    _login(client, tenant_a.slug, users_a["admin"].email)

    resp = client.get(f"/ui/admin/customers/{customer_b.id}/ml", follow_redirects=False)
    assert resp.status_code == 404


def test_set_customer_ml_mode(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="web-cust-ml-mode")
    customer, _account2 = _make_customer(db_session, tenant)
    _login(client, tenant.slug, users["admin"].email)
    client.get(f"/ui/admin/customers/{customer.id}/ml")

    resp = client.post(
        f"/ui/admin/customers/{customer.id}/ml/mode",
        data={"network_code": "check", "mode": "global", "csrf_token": _csrf(client)},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert "error" not in resp.headers["location"]

    from openclear.domain.customer_ml_setting import MlScoringMode
    from openclear.services import customer_ml_service

    db_session.expire_all()
    assert customer_ml_service.get_mode(db_session, tenant.id, customer.id, "check") == MlScoringMode.GLOBAL


def test_retrain_customer_model_via_web(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="web-cust-ml-retrain")
    customer, customer_account = _make_customer(db_session, tenant)
    _seed_labeled_decisions(db_session, tenant, customer_account, users)
    _login(client, tenant.slug, users["admin"].email)
    client.get(f"/ui/admin/customers/{customer.id}/ml")

    resp = client.post(
        f"/ui/admin/customers/{customer.id}/ml/retrain",
        data={"network_code": "check", "csrf_token": _csrf(client)},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert "error" not in resp.headers["location"]

    from openclear.services import customer_ml_service

    db_session.expire_all()
    models = customer_ml_service.list_customer_models(db_session, customer.id, "check")
    assert len(models) == 1
    assert models[0].status == MlModelStatus.ACTIVE


def test_retrain_customer_model_insufficient_data_shows_error(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="web-cust-ml-retrain-fail")
    customer, _account2 = _make_customer(db_session, tenant)
    _login(client, tenant.slug, users["admin"].email)
    client.get(f"/ui/admin/customers/{customer.id}/ml")

    resp = client.post(
        f"/ui/admin/customers/{customer.id}/ml/retrain",
        data={"network_code": "check", "csrf_token": _csrf(client)},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert "error" in resp.headers["location"]


def test_activate_customer_model_via_web(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="web-cust-ml-activate")
    customer, customer_account = _make_customer(db_session, tenant)
    _seed_labeled_decisions(db_session, tenant, customer_account, users)

    from openclear.ml.train import train_model

    first_result = train_model(db_session, "check", customer_id=customer.id)
    second = create_model_row(
        db_session,
        network_code="check",
        customer_id=customer.id,
        version="v-test-second",
        algorithm="logistic_regression",
        artifact_path=first_result.model_row.artifact_path,
        trained_from_decision_count=12,
        metrics_json={},
        status=MlModelStatus.RETIRED,
    )
    db_session.commit()

    _login(client, tenant.slug, users["admin"].email)
    client.get(f"/ui/admin/customers/{customer.id}/ml")

    resp = client.post(
        f"/ui/admin/customers/{customer.id}/ml/models/{second.id}/activate",
        data={"csrf_token": _csrf(client)},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert "error" not in resp.headers["location"]

    db_session.expire_all()
    db_session.refresh(second)
    first_row = db_session.get(type(second), first_result.model_row.id)
    db_session.refresh(first_row)
    assert second.status == MlModelStatus.ACTIVE
    assert first_row.status == MlModelStatus.RETIRED


def test_activate_customer_model_rejects_another_customers_model(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="web-cust-ml-activate-xcust")
    customer_a, _account_a = _make_customer(db_session, tenant, "C-A")
    customer_b, _account_b = _make_customer(db_session, tenant, "C-B")
    other_customers_model = create_model_row(
        db_session, network_code="check", customer_id=customer_b.id, version="v1", algorithm="logistic_regression",
        artifact_path="/tmp/fake.joblib", trained_from_decision_count=12, metrics_json={}, status=MlModelStatus.TRAINING,
    )
    db_session.commit()

    _login(client, tenant.slug, users["admin"].email)
    client.get(f"/ui/admin/customers/{customer_a.id}/ml")

    resp = client.post(
        f"/ui/admin/customers/{customer_a.id}/ml/models/{other_customers_model.id}/activate",
        data={"csrf_token": _csrf(client)},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert "error" in resp.headers["location"]

    db_session.expire_all()
    db_session.refresh(other_customers_model)
    assert other_customers_model.status == MlModelStatus.TRAINING


def test_customer_ml_page_shows_default_disposition_table(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="web-cust-disp-show")
    customer, _account2 = _make_customer(db_session, tenant)
    _login(client, tenant.slug, users["admin"].email)

    resp = client.get(f"/ui/admin/customers/{customer.id}/ml")
    assert resp.status_code == 200
    assert "Default disposition" in resp.text


def test_set_customer_disposition_fixed_pay(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="web-cust-disp-fixed-pay")
    customer, _account2 = _make_customer(db_session, tenant)
    _login(client, tenant.slug, users["admin"].email)
    client.get(f"/ui/admin/customers/{customer.id}/ml")

    resp = client.post(
        f"/ui/admin/customers/{customer.id}/disposition",
        data={"network_code": "check", "mode": "fixed_pay", "response_window_hours": "12", "csrf_token": _csrf(client)},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert "error" not in resp.headers["location"]

    from openclear.domain.customer_disposition_setting import DispositionMode
    from openclear.services import auto_disposition_service

    db_session.expire_all()
    assert auto_disposition_service.get_disposition_mode(db_session, tenant.id, customer.id, "check") == DispositionMode.FIXED_PAY
    assert auto_disposition_service.get_response_window_hours(db_session, tenant.id, customer.id, "check") == 12


def test_set_customer_disposition_with_ach_return_reason(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="web-cust-disp-ach-reason")
    customer, _account2 = _make_customer(db_session, tenant)
    _login(client, tenant.slug, users["admin"].email)
    client.get(f"/ui/admin/customers/{customer.id}/ml")

    from openclear.services import ach_return_reason_service, auto_disposition_service

    reason = ach_return_reason_service.list_ach_return_reasons(db_session, tenant.id, active_only=True)[0]

    resp = client.post(
        f"/ui/admin/customers/{customer.id}/disposition",
        data={
            "network_code": "ach", "mode": "fixed_return", "response_window_hours": "",
            "default_ach_return_reason_id": str(reason.id), "csrf_token": _csrf(client),
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert "error" not in resp.headers["location"]

    db_session.expire_all()
    summary = {s.network_code: s for s in auto_disposition_service.get_disposition_summary(db_session, tenant.id, customer.id)}
    assert summary["ach"].default_ach_return_reason_id == reason.id


def test_set_customer_disposition_rejects_non_positive_window(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="web-cust-disp-bad-window")
    customer, _account2 = _make_customer(db_session, tenant)
    _login(client, tenant.slug, users["admin"].email)
    client.get(f"/ui/admin/customers/{customer.id}/ml")

    resp = client.post(
        f"/ui/admin/customers/{customer.id}/disposition",
        data={"network_code": "check", "mode": "fixed_pay", "response_window_hours": "0", "csrf_token": _csrf(client)},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert "error" in resp.headers["location"]


def test_customer_detail_page_shows_ml_scoring_card_for_admin_only(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="web-cust-ml-card")
    customer, _account2 = _make_customer(db_session, tenant)

    customer_manage_only_group = security_group_service.create_security_group(
        db_session, tenant.id, security_group_service.SecurityGroupInput(name="Customer Manager", permissions=["customer:manage"])
    )
    limited_user = user_service.create_user_with_membership(
        db_session, tenant.id, email="custmgr@web-cust-ml-card.example.com", password=TenantFactory.PASSWORD,
        security_group_id=customer_manage_only_group.id,
    )
    db_session.commit()

    _login(client, tenant.slug, users["admin"].email)
    admin_resp = client.get(f"/ui/customers/{customer.id}")
    assert "ML scoring" in admin_resp.text

    client.cookies.clear()
    _login(client, tenant.slug, limited_user.email)
    limited_resp = client.get(f"/ui/customers/{customer.id}")
    assert "ML scoring" not in limited_resp.text
