# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Billing in the web UI (BILLING_PLAN.md B3)."""

import csv
import io

import pytest

from openclear.services import security_group_service, user_service
from openclear.services.billing import terms as billing_terms
from openclear.services.doc_pdf_service import weasyprint_usable
from tests.conftest import TenantFactory
from tests.test_api.test_billing import PERIOD_PARAM, _bank_with_activity


def _login(client, tenant_slug, email):
    client.get("/ui/login")
    csrf = client.cookies.get("csrf_token")
    resp = client.post(
        "/ui/login",
        data={"tenant_slug": tenant_slug, "email": email, "password": TenantFactory.PASSWORD, "csrf_token": csrf, "next": "/ui/"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    return csrf


@pytest.fixture
def bank(client, db_session, tenant_factory):
    tenant, users, analysis, hard, accounts = _bank_with_activity(db_session, tenant_factory, "web-billing")
    csrf = _login(client, tenant.slug, users["admin"].email)
    return tenant, users, analysis, hard, accounts, csrf


def test_overview_finalize_and_downloads(client, bank):
    tenant, _users, analysis, hard, _accounts, csrf = bank

    page = client.get(f"/ui/billing?period={PERIOD_PARAM}")
    assert page.status_code == 200
    assert "Not finalized" in page.text
    assert "Analysis Co" in page.text and "Hard Charge Co" in page.text
    assert "$25.10" in page.text  # the hard-charge customer's total
    assert "available once the month is finalized" in page.text

    finalized = client.post("/ui/billing/finalize", data={"csrf_token": csrf, "period": PERIOD_PARAM}, follow_redirects=False)
    assert finalized.status_code == 303
    page = client.get(finalized.headers["location"])
    assert "Finalized" in page.text and "version 1" in page.text

    report = client.get(f"/ui/billing/report.csv?period={PERIOD_PARAM}&view=finalized")
    assert report.status_code == 200
    assert {r["customer_number"] for r in csv.DictReader(io.StringIO(report.text))} == {"A-1", "H-1", ""}
    for name, kind in (("analysis-feed.csv", "text/csv"), ("hard-charge-postings.csv", "text/csv"), ("camt086.xml", "application/xml")):
        resp = client.get(f"/ui/billing/export/{name}?period={PERIOD_PARAM}")
        assert resp.status_code == 200 and resp.headers["content-type"].startswith(kind), name


def test_statement_page_and_pdf(client, bank):
    tenant, _users, analysis, hard, _accounts, _csrf = bank

    page = client.get(f"/ui/billing/statement?period={PERIOD_PARAM}&customer={analysis.id}")
    assert page.status_code == 200
    assert "Analysis Co" in page.text and "Estimate (not finalized)" in page.text
    assert "A-100: 3, A-200: 2" in page.text  # volumes per account
    assert client.get(f"/ui/billing/statement?period={PERIOD_PARAM}&customer=bank").status_code == 200

    if weasyprint_usable():
        pdf = client.get(f"/ui/billing/statement.pdf?period={PERIOD_PARAM}&customer={analysis.id}")
        assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")


def test_fee_schedule_form(client, db_session, bank):
    tenant, _users, *_rest, csrf = bank

    created = client.post("/ui/billing/fees", data={
        "csrf_token": csrf, "code": "pp-tiered", "description": "Tiered items", "metric": "presented_checks",
        "unit_price": "0", "tiers": "2 0.00\n* 0.25", "minimum_charge": "", "afp_code": "150120", "bank_service_code": "",
        "effective_from": "", "effective_to": "", "sort_order": "5", "is_active": "on",
    }, follow_redirects=False)
    assert created.status_code == 303
    fee = next(f for f in billing_terms.list_fee_items(db_session, tenant.id) if f.code == "PP-TIERED")
    assert fee.tiers == [{"up_to": 2, "unit_price": "0.00"}, {"up_to": None, "unit_price": "0.25"}]

    bad = client.post("/ui/billing/fees", data={
        "csrf_token": csrf, "code": "bad", "description": "Bad tiers", "metric": "presented_checks", "unit_price": "0",
        "tiers": "10 0.10\n5 0.20", "minimum_charge": "", "afp_code": "", "bank_service_code": "",
        "effective_from": "", "effective_to": "", "sort_order": "0", "is_active": "on",
    })
    assert bad.status_code == 422
    assert "higher than the one before" in bad.text

    edit = client.get(f"/ui/billing/fees/{fee.id}/edit")
    assert edit.status_code == 200 and "* 0.25" in edit.text

    starter = client.post("/ui/billing/fees/starter", data={"csrf_token": csrf}, follow_redirects=False)
    assert starter.status_code == 303
    assert "ACHPP-ITEM" in client.get("/ui/billing/fees").text


def test_settings_and_customer_terms(client, db_session, bank):
    tenant, _users, analysis, hard, accounts, csrf = bank
    fees = {f.code: f for f in billing_terms.list_fee_items(db_session, tenant.id)}

    saved = client.post(f"/ui/customers/{analysis.id}/billing", data={
        "csrf_token": csrf, "billing_method": "hard_charge", "billing_account_id": str(accounts["A-200"].id),
        "discount_percent": "0", "notes": "", f"override_waived_{fees['PP-MAINT'].id}": "on",
    }, follow_redirects=False)
    assert saved.status_code == 303
    terms, overrides = billing_terms.get_customer_terms(db_session, tenant.id, analysis.id)
    assert terms.billing_method.value == "hard_charge" and [o.waived for o in overrides] == [True]
    assert "Billing terms" in client.get(f"/ui/customers/{analysis.id}").text

    bad = client.post(f"/ui/customers/{analysis.id}/billing", data={
        "csrf_token": csrf, "billing_method": "analysis", "billing_account_id": str(accounts["H-100"].id), "discount_percent": "0",
    })
    assert bad.status_code == 422 and "one of this customer" in bad.text

    settings = client.post("/ui/billing/settings", data={
        "csrf_token": csrf, "customer_default_billing_method": "hard_charge", "bank_billing_method": "exempt", "bank_billing_account_id": "",
    }, follow_redirects=False)
    assert settings.status_code == 303
    db_session.expire_all()
    assert billing_terms.get_bank_settings(db_session, tenant.id).customer_default_billing_method.value == "hard_charge"


def test_customer_users_see_only_their_own_statement(client, db_session, tenant_factory):
    tenant, users, analysis, hard, accounts = _bank_with_activity(db_session, tenant_factory, "web-billing-cust")
    group = security_group_service.create_security_group(
        db_session, tenant.id, security_group_service.SecurityGroupInput(name="Customer Billing", permissions=["billing:read", "exception:read"])
    )
    user_service.create_user_with_membership(
        db_session, tenant.id, email="statement@customer.example.com", password=TenantFactory.PASSWORD,
        security_group_id=group.id, customer_id=hard.id,
    )
    db_session.commit()
    _login(client, tenant.slug, "statement@customer.example.com")

    overview = client.get(f"/ui/billing?period={PERIOD_PARAM}", follow_redirects=False)
    assert overview.status_code == 303 and overview.headers["location"].startswith("/ui/billing/statement")
    mine = client.get(f"/ui/billing/statement?period={PERIOD_PARAM}&customer={analysis.id}")
    assert "Hard Charge Co" in mine.text and "Analysis Co" not in mine.text
    assert "Billing Statement" in mine.text  # its own nav link
    assert client.get(f"/ui/billing/export/hard-charge-postings.csv?period={PERIOD_PARAM}").status_code == 403
    assert client.get("/ui/billing/fees").status_code == 403


def test_billing_is_admin_only_by_default(client, db_session, tenant_factory):
    tenant, users, *_ = _bank_with_activity(db_session, tenant_factory, "web-billing-viewer")
    _login(client, tenant.slug, users["viewer"].email)
    assert client.get("/ui/billing").status_code == 403
    assert "/ui/billing" not in client.get("/ui/").text
