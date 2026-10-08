# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Billing (BILLING_PLAN.md phase B1): pricing, metrics, statements and the API."""

import csv
import io
from datetime import date
from decimal import Decimal

import pytest

from openclear.domain.billing import BillingMethod
from openclear.networks.check.ingestion import PaidItemSubmission, ingest_paid_item
from openclear.services import account_service, customer_service, issued_item_service, security_group_service, user_service
from openclear.services.billing import terms as billing_terms
from openclear.services.billing.pricing import price_fee
from openclear.services.billing.statements import build_statements, month_period
from tests.conftest import TenantFactory, login_headers

TODAY = date.today()
PERIOD = month_period(TODAY.year, TODAY.month)
PERIOD_PARAM = f"{TODAY.year}-{TODAY.month:02d}"


# --- Pricing ---------------------------------------------------------------------------


def _price(**overrides):
    args = dict(volume=10, unit_price=Decimal("0.25"), tiers=None, minimum_charge=None, billing_method=BillingMethod.ANALYSIS)
    args.update(overrides)
    return price_fee(**args)


def test_unit_priced_analysis_line_is_balance_compensable():
    line = _price()
    assert (line.list_amount, line.net_amount, line.charge_method, line.payment_method) == (Decimal("2.50"), Decimal("2.50"), "UPRC", "BCMP")


def test_hard_charge_line_is_flat():
    assert _price(billing_method=BillingMethod.HARD_CHARGE).payment_method == "FLAT"


def test_step_tiers_charge_each_tiers_units_at_its_price():
    line = _price(volume=250, tiers=[{"up_to": 100, "unit_price": "0"}, {"up_to": 200, "unit_price": "0.10"}, {"up_to": None, "unit_price": "0.05"}])
    # 100 free, 100 at 0.10, 50 at 0.05
    assert line.list_amount == Decimal("12.50")
    assert line.charge_method == "LPRC"
    assert [t["quantity"] for t in line.tiers] == [100, 100, 50]
    assert line.unit_price == Decimal("0.0500")  # blended 12.50 / 250


def test_minimum_charge_applies_when_the_service_was_used():
    line = _price(volume=2, minimum_charge=Decimal("15.00"))
    assert (line.list_amount, line.charge_method) == (Decimal("15.00"), "MCHG")
    assert _price(volume=0, minimum_charge=Decimal("15.00")).list_amount == Decimal("0.00")


def test_discount_negotiated_price_waiver_and_exempt():
    discounted = _price(discount_percent=Decimal("20"))
    assert (discounted.discount_amount, discounted.net_amount, discounted.charge_method) == (Decimal("0.50"), Decimal("2.00"), "DPRC")
    assert _price(override_unit_price=Decimal("0.10")).net_amount == Decimal("1.00")
    waived = _price(waived=True)
    assert (waived.list_amount, waived.net_amount, waived.payment_method) == (Decimal("2.50"), Decimal("0.00"), "WVED")
    assert _price(billing_method=BillingMethod.EXEMPT).payment_method == "WVED"
    assert _price(unit_price=Decimal("0")).payment_method == "FREE"


def test_amounts_round_half_up_to_cents():
    assert _price(volume=3, unit_price=Decimal("0.0350")).list_amount == Decimal("0.11")  # 0.105


# --- Statements --------------------------------------------------------------------------


def _bank_with_activity(db_session, tenant_factory, slug):
    tenant, bank_account, users = tenant_factory.make(slug=slug)
    analysis = customer_service.create_customer(db_session, tenant.id, customer_service.CustomerInput(customer_number="A-1", name="Analysis Co"))
    hard = customer_service.create_customer(db_session, tenant.id, customer_service.CustomerInput(customer_number="H-1", name="Hard Charge Co"))
    accounts = {}
    for customer, numbers in ((analysis, ("A-100", "A-200")), (hard, ("H-100",))):
        for number in numbers:
            accounts[number] = account_service.create_account(
                db_session, tenant.id, account_service.AccountInput(account_number=number, name="Op", customer_id=customer.id)
            )
    db_session.commit()

    def present(account, check_number, issued=True):
        if issued:
            issued_item_service.create_issued_item(
                db_session, tenant.id,
                issued_item_service.IssuedItemInput(account_id=account.id, check_number=check_number, amount=Decimal("50.00"), payee_name="X", issue_date=TODAY),
                submitted_by_user_id=users["preparer"].id,
            )
        ingest_paid_item(db_session, tenant.id, PaidItemSubmission(account_id=account.id, check_number=check_number, presented_amount=Decimal("50.00"), presented_date=TODAY))
        db_session.commit()

    for n in range(3):
        present(accounts["A-100"], f"10{n}")
    present(accounts["A-200"], "200")
    present(accounts["A-200"], "201", issued=False)  # not in file -> exception
    present(accounts["H-100"], "300")
    present(bank_account, "900")  # the bank's own account

    billing_terms.set_customer_terms(db_session, tenant.id, hard.id, billing_method=BillingMethod.HARD_CHARGE, billing_account_id=accounts["H-100"].id)
    billing_terms.update_bank_settings(db_session, tenant.id, customer_default_billing_method=BillingMethod.ANALYSIS,
                                       bank_billing_method=BillingMethod.EXEMPT, bank_billing_account_id=bank_account.id)
    billing_terms.create_fee_item(db_session, tenant.id, code="PP-ITEM", description="Presented check", metric="presented_checks",
                                  unit_price="0.10", afp_code="150120")
    billing_terms.create_fee_item(db_session, tenant.id, code="PP-EXCPT", description="Exception", metric="check_exceptions", unit_price="1.50")
    billing_terms.create_fee_item(db_session, tenant.id, code="PP-MAINT", description="Maintenance", metric="customer_monthly", unit_price="25.00")
    db_session.commit()
    return tenant, users, analysis, hard, accounts


def test_statements_price_each_customer_by_its_billing_method(db_session, tenant_factory):
    tenant, _users, analysis, hard, _accounts = _bank_with_activity(db_session, tenant_factory, "billing-stmts")

    statements = {s.customer_number: s for s in build_statements(db_session, tenant.id, PERIOD)}

    a = statements["A-1"]
    assert a.billing_method == BillingMethod.ANALYSIS and a.billing_account_number == "A-100"  # defaults to its first account
    lines = {line.code: line for line in a.lines}
    assert lines["PP-ITEM"].priced.volume == 5
    assert lines["PP-ITEM"].accounts == {"A-100": 3, "A-200": 2}
    assert lines["PP-ITEM"].afp_code == "150120"
    assert lines["PP-EXCPT"].priced.volume == 1
    assert {line.priced.payment_method for line in a.lines} == {"BCMP"}
    assert a.net_total == Decimal("0.50") + Decimal("1.50") + Decimal("25.00")

    h = statements["H-1"]
    assert (h.billing_method, h.billing_account_number) == (BillingMethod.HARD_CHARGE, "H-100")
    assert {line.priced.payment_method for line in h.lines} == {"FLAT"}
    assert h.net_total == Decimal("25.10")

    bank = statements[None]
    assert bank.billing_method == BillingMethod.EXEMPT
    assert [(line.code, line.priced.volume) for line in bank.lines] == [("PP-ITEM", 1)]
    assert bank.net_total == Decimal("0.00") and bank.waived_total == Decimal("0.10")


def test_a_period_with_no_activity_still_bills_monthly_fees_only(db_session, tenant_factory):
    tenant, *_ = _bank_with_activity(db_session, tenant_factory, "billing-quiet")
    statements = build_statements(db_session, tenant.id, month_period(TODAY.year + 1, TODAY.month))
    assert {(s.customer_number, tuple(line.code for line in s.lines)) for s in statements} == {("A-1", ("PP-MAINT",)), ("H-1", ("PP-MAINT",))}


def test_waive_all_and_overrides(db_session, tenant_factory):
    tenant, _users, analysis, hard, accounts = _bank_with_activity(db_session, tenant_factory, "billing-overrides")
    fees = {f.code: f for f in billing_terms.list_fee_items(db_session, tenant.id)}
    billing_terms.set_customer_terms(db_session, tenant.id, analysis.id, billing_method=BillingMethod.ANALYSIS, discount_percent="10",
                                     overrides=[{"fee_item_id": fees["PP-MAINT"].id, "waived": True}, {"fee_item_id": fees["PP-ITEM"].id, "unit_price": "0.05"}])
    billing_terms.set_customer_terms(db_session, tenant.id, hard.id, billing_method=BillingMethod.HARD_CHARGE, waive_all=True)
    db_session.commit()

    statements = {s.customer_number: s for s in build_statements(db_session, tenant.id, PERIOD)}
    a = {line.code: line.priced for line in statements["A-1"].lines}
    assert a["PP-MAINT"].payment_method == "WVED" and a["PP-MAINT"].net_amount == Decimal("0.00")
    assert a["PP-ITEM"].net_amount == Decimal("0.22")  # 5 x 0.05 = 0.25, less 10% (0.025, rounded up to 0.03)
    assert statements["H-1"].net_total == Decimal("0.00")
    assert {line.priced.payment_method for line in statements["H-1"].lines} == {"WVED"}


def test_terms_validation(db_session, tenant_factory):
    tenant, _users, analysis, hard, accounts = _bank_with_activity(db_session, tenant_factory, "billing-validate")
    with pytest.raises(ValueError, match="one of this customer's accounts"):
        billing_terms.set_customer_terms(db_session, tenant.id, analysis.id, billing_method=BillingMethod.ANALYSIS, billing_account_id=accounts["H-100"].id)
    with pytest.raises(ValueError, match="more than 100"):
        billing_terms.set_customer_terms(db_session, tenant.id, analysis.id, billing_method=BillingMethod.ANALYSIS, discount_percent="150")
    with pytest.raises(ValueError, match="Unknown metric"):
        billing_terms.create_fee_item(db_session, tenant.id, code="BAD", description="x", metric="nonsense")
    with pytest.raises(ValueError, match="last tier must be open-ended"):
        billing_terms.create_fee_item(db_session, tenant.id, code="TIERS", description="x", metric="presented_checks", tiers=[{"up_to": 10, "unit_price": "1"}])
    with pytest.raises(ValueError, match="already exists"):
        billing_terms.create_fee_item(db_session, tenant.id, code="pp-item", description="x", metric="presented_checks")


# --- API ---------------------------------------------------------------------------------


def test_api_fee_schedule_terms_statements_and_report(client, db_session, tenant_factory):
    tenant, users, analysis, hard, accounts = _bank_with_activity(db_session, tenant_factory, "billing-api")
    headers = login_headers(client, tenant.slug, users["admin"].email)

    metrics = client.get("/api/v1/billing/metrics", headers=headers).json()
    assert {"presented_checks", "items_returned", "user_monthly", "account_monthly", "sms_sent"} <= {m["code"] for m in metrics}

    created = client.post("/api/v1/billing/fee-items", headers=headers, json={
        "code": "pp-tier", "description": "Tiered items", "metric": "issued_items",
        "tiers": [{"up_to": 2, "unit_price": "0"}, {"up_to": None, "unit_price": "0.20"}],
    })
    assert created.status_code == 201, created.text
    assert created.json()["code"] == "PP-TIER"
    assert client.post("/api/v1/billing/fee-items", headers=headers, json={"code": "x", "description": "x", "metric": "nope"}).status_code == 422

    starter = client.post("/api/v1/billing/fee-items/starter-schedule", headers=headers)
    assert starter.status_code == 201
    assert "PP-ITEM" not in {f["code"] for f in starter.json()}  # already had that code

    terms = client.put(f"/api/v1/billing/customers/{analysis.id}/terms", headers=headers, json={
        "billing_method": "hard_charge", "billing_account_id": str(accounts["A-200"].id), "discount_percent": "5",
    })
    assert terms.status_code == 200, terms.text
    assert terms.json()["billing_method"] == "hard_charge" and terms.json()["is_default"] is False

    statements = client.get(f"/api/v1/billing/statements?period={PERIOD_PARAM}", headers=headers)
    assert statements.status_code == 200
    by_customer = {s["customer_number"]: s for s in statements.json()}
    assert by_customer["A-1"]["billing_method"] == "hard_charge"
    assert by_customer["A-1"]["billing_account_number"] == "A-200"
    tier_line = next(line for line in by_customer["A-1"]["lines"] if line["fee_code"] == "PP-TIER")
    assert (tier_line["volume"], tier_line["list_amount"]) == (4, "0.40")  # 2 free, 2 at 0.20

    report = client.get(f"/api/v1/billing/report.csv?period={PERIOD_PARAM}", headers=headers)
    assert report.status_code == 200 and report.headers["content-type"].startswith("text/csv")
    rows = list(csv.DictReader(io.StringIO(report.text)))
    assert {r["customer_number"] for r in rows} == {"A-1", "H-1", ""}
    assert any(r["afp_code"] == "150120" for r in rows)

    assert client.get("/api/v1/billing/statements?period=2026-13", headers=headers).status_code == 422


def test_billing_permissions(client, db_session, tenant_factory):
    tenant, users, analysis, hard, accounts = _bank_with_activity(db_session, tenant_factory, "billing-perms")

    viewer = login_headers(client, tenant.slug, users["viewer"].email)
    assert client.get(f"/api/v1/billing/statements?period={PERIOD_PARAM}", headers=viewer).status_code == 403
    assert client.get("/api/v1/billing/fee-items", headers=viewer).status_code == 403

    # A customer's own user, in a group that (nominally) has both billing permissions.
    group = security_group_service.create_security_group(
        db_session, tenant.id, security_group_service.SecurityGroupInput(name="Customer Billing", permissions=["billing:read", "billing:manage"])
    )
    user_service.create_user_with_membership(
        db_session, tenant.id, email="billing@customer.example.com", password=TenantFactory.PASSWORD,
        security_group_id=group.id, customer_id=analysis.id,
    )
    db_session.commit()
    customer_user = login_headers(client, tenant.slug, "billing@customer.example.com")

    mine = client.get(f"/api/v1/billing/statements?period={PERIOD_PARAM}&customer_id={hard.id}", headers=customer_user)
    assert mine.status_code == 200
    assert [s["customer_number"] for s in mine.json()] == ["A-1"]  # never another customer's, nor the bank-wide one
    assert client.get("/api/v1/billing/fee-items", headers=customer_user).status_code == 403  # billing:manage is masked
