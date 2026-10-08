# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Code that works across banks, with Postgres row-level security enforced (the app runs
as a restricted role; see conftest.py): switching organization, the scheduled jobs, the
platform operator's usage report, and creating a new bank. Runs only with
OPENCLEAR_TEST_DATABASE_URL set."""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from openclear.domain.ach_transaction import AchSettlementStatus, AchTransaction, AchTransactionType
from openclear.domain.customer_disposition_setting import DispositionMode
from openclear.domain.exception_item import ExceptionItem, ExceptionStatus
from openclear.domain.issued_item import IssuedItem
from openclear.networks.ach.ingestion import AchTransactionSubmission, ingest_ach_transaction
from openclear.repositories.exception_repo import ExceptionRepository
from openclear.services import (
    ach_return_reason_service,
    auto_disposition_service,
    provisioning_service,
    security_group_service,
    usage_metrics_service,
    user_service,
)
from openclear.workers import tasks
from tests.conftest import TEST_DATABASE_URL, TenantFactory
from tests.test_auth.test_sweep_expired_dispositions_job import _expired_exception, _make_customer
from tests.test_bulk_import.test_dropbox_import_service import _configure_dropbox, _drop, _issued_items_csv

pytestmark = pytest.mark.skipif(not TEST_DATABASE_URL, reason="needs OPENCLEAR_TEST_DATABASE_URL (Postgres)")


def _web_login(client, tenant_slug, email):
    client.get("/ui/login")
    csrf = client.cookies.get("csrf_token")
    resp = client.post(
        "/ui/login",
        data={"tenant_slug": tenant_slug, "email": email, "password": TenantFactory.PASSWORD, "csrf_token": csrf, "next": "/ui/"},
        follow_redirects=False,
    )
    assert resp.status_code == 303, resp.text[:300]
    return csrf


def test_switching_organization_lists_and_enters_the_other_bank(rls_client, db_session, tenant_factory):
    tenant_a, _account_a, users_a = tenant_factory.make(name="Switch RLS A", slug="rls-switch-a")
    tenant_b, _account_b, _users_b = tenant_factory.make(name="Switch RLS B", slug="rls-switch-b")
    group_b = security_group_service.get_security_group_by_name(db_session, tenant_b.id, "Admin")
    membership_b = user_service.confirm_cross_tenant_membership(
        db_session, tenant_b.id, email=users_a["admin"].email, security_group_id=group_b.id
    )
    db_session.commit()

    csrf = _web_login(rls_client, tenant_a.slug, users_a["admin"].email)
    page = rls_client.get("/ui/switch-tenant", follow_redirects=False)
    assert page.status_code == 200
    assert f">{tenant_b.name} (bank-wide)</button>" in page.text

    switched = rls_client.post(
        "/ui/switch-tenant", data={"csrf_token": csrf, "membership_id": str(membership_b.id)}, follow_redirects=False
    )
    assert switched.status_code == 303
    assert switched.headers["location"] == "/ui/"
    groups = rls_client.get("/ui/security-groups", follow_redirects=False)
    assert groups.status_code == 200
    assert tenant_b.name in groups.text


def test_the_expired_disposition_sweep_settles_the_item(monkeypatch, app_session_factory, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="rls-sweep")
    customer, account = _make_customer(db_session, tenant, number="RLS-S")
    auto_disposition_service.set_disposition_setting(
        db_session, tenant.id, customer.id, "check",
        mode=DispositionMode.FIXED_PAY, response_window_hours=1, default_ach_return_reason_id=None,
    )
    db_session.commit()
    exception = _expired_exception(db_session, tenant, account, users, "R001")

    monkeypatch.setattr(tasks, "get_session_factory", lambda: app_session_factory)
    tasks.sweep_expired_dispositions_job()

    db_session.expire_all()
    # With no bank bound, the customer's disposition setting (an RLS table) was invisible,
    # so the sweep read "no setting" and left the exception open.
    assert db_session.get(ExceptionItem, exception.id).status == ExceptionStatus.PAY


def test_the_sweep_returns_an_ach_debit_and_settles_the_transaction(monkeypatch, app_session_factory, db_session, tenant_factory):
    tenant, _account, _users = tenant_factory.make(slug="rls-sweep-ach")
    customer, account = _make_customer(db_session, tenant, number="RLS-A")
    reason = ach_return_reason_service.list_ach_return_reasons(db_session, tenant.id)[0]
    auto_disposition_service.set_disposition_setting(
        db_session, tenant.id, customer.id, "ach",
        mode=DispositionMode.FIXED_RETURN, response_window_hours=1, default_ach_return_reason_id=reason.id,
    )
    db_session.commit()
    txn = ingest_ach_transaction(
        db_session, tenant.id,
        AchTransactionSubmission(
            account_id=account.id, originator_id="UNKNOWN", originator_name="Unknown Co", amount=Decimal("75.00"),
            transaction_type=AchTransactionType.DEBIT, sec_code="WEB", trace_number="RLS-TRACE-1", effective_date=date(2026, 1, 10),
        ),
    )
    db_session.commit()
    exception = ExceptionRepository(db_session, tenant.id).list(source_item_id=txn.id)[0]
    exception.decision_deadline = datetime.now(timezone.utc) - timedelta(hours=1)
    db_session.commit()

    monkeypatch.setattr(tasks, "get_session_factory", lambda: app_session_factory)
    tasks.sweep_expired_dispositions_job()

    db_session.expire_all()
    assert db_session.get(ExceptionItem, exception.id).status == ExceptionStatus.RETURN
    # The update that would otherwise have matched no rows under RLS.
    assert db_session.get(AchTransaction, txn.id).settlement_status == AchSettlementStatus.RETURNED


def test_the_scheduled_dropbox_import_loads_each_banks_files(monkeypatch, tmp_path, app_session_factory, db_session, tenant_factory):
    root = _configure_dropbox(monkeypatch, tmp_path)
    bank_a, account_a, _users_a = tenant_factory.make(slug="rls-dropbox-a")
    bank_b, account_b, _users_b = tenant_factory.make(slug="rls-dropbox-b")
    _drop(root, bank_a.slug, "issued_items", "a.csv", _issued_items_csv([(account_a.account_number, "8101", "10.00")]))
    _drop(root, bank_b.slug, "issued_items", "b.csv", _issued_items_csv([(account_b.account_number, "8201", "20.00")]))

    monkeypatch.setattr(tasks, "get_session_factory", lambda: app_session_factory)
    tasks.dropbox_import_job()

    db_session.expire_all()
    checks = {(i.tenant_id, i.check_number) for i in db_session.query(IssuedItem).all()}
    assert (bank_a.id, "8101") in checks
    assert (bank_b.id, "8201") in checks


def test_the_usage_report_counts_every_bank(app_session_factory, tenant_factory):
    bank_a, _a, users_a = tenant_factory.make(slug="rls-usage-a")
    bank_b, _b, _users_b = tenant_factory.make(slug="rls-usage-b")

    with app_session_factory() as session:
        usage = {u.tenant_id: u for u in usage_metrics_service.get_all_tenants_usage(session, date.today() - timedelta(days=1), date.today())}

    assert usage[bank_a.id].accounts == 1
    assert usage[bank_b.id].accounts == 1
    assert usage[bank_a.id].users == len(users_a)


def test_creating_a_new_bank_works_as_the_restricted_role(app_session_factory, rls_client):
    with app_session_factory() as session:
        identity = provisioning_service.create_tenant_with_admin(
            session, tenant_name="Brand New RLS Bank", tenant_slug="rls-new-bank",
            admin_email="admin@rls-new-bank.example.com", admin_password=TenantFactory.PASSWORD,
        )
        session.commit()
        slug = identity.tenant.slug

    _web_login(rls_client, slug, "admin@rls-new-bank.example.com")
    assert rls_client.get("/ui/", follow_redirects=False).status_code == 200


def test_billing_statements_see_only_this_banks_activity(rls_client, db_session, tenant_factory):
    from tests.conftest import login_headers
    from tests.test_api.test_billing import PERIOD_PARAM, _bank_with_activity

    tenant, users, *_ = _bank_with_activity(db_session, tenant_factory, "rls-billing-a")
    _bank_with_activity(db_session, tenant_factory, "rls-billing-b")  # same customer numbers, another bank

    headers = login_headers(rls_client, tenant.slug, users["admin"].email)
    resp = rls_client.get(f"/api/v1/billing/statements?period={PERIOD_PARAM}", headers=headers)

    assert resp.status_code == 200, resp.text
    by_customer = {s["customer_number"]: s for s in resp.json()}
    assert set(by_customer) == {"A-1", "H-1", None}
    item_line = next(line for line in by_customer["A-1"]["lines"] if line["fee_code"] == "PP-ITEM")
    assert item_line["volume"] == 5  # not doubled by the other bank's identical activity
