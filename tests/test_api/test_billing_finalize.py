# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Billing phase B2 (BILLING_PLAN.md): finalizing a period, and the exports produced from
it (analysis feed, hard-charge postings, camt.086)."""

import csv
import io
import xml.etree.ElementTree as ET
from decimal import Decimal

from openclear.domain.audit_log_entry import AuditLogEntry
from openclear.services import security_group_service, user_service
from openclear.services.billing import terms as billing_terms
from tests.conftest import TenantFactory, login_headers
from tests.test_api.test_billing import PERIOD_PARAM, _bank_with_activity

NS = {"c": "urn:iso:std:iso:20022:tech:xsd:camt.086.001.04"}


def _setup(client, db_session, tenant_factory, slug):
    tenant, users, analysis, hard, accounts = _bank_with_activity(db_session, tenant_factory, slug)
    return tenant, users, analysis, hard, accounts, login_headers(client, tenant.slug, users["admin"].email)


def test_finalize_freezes_the_period_and_refinalizing_replaces_it(client, db_session, tenant_factory):
    tenant, _users, _a, _h, _acc, headers = _setup(client, db_session, tenant_factory, "billing-final")
    live = client.get(f"/api/v1/billing/statements?period={PERIOD_PARAM}", headers=headers).json()

    first = client.post(f"/api/v1/billing/periods/{PERIOD_PARAM}/finalize", headers=headers)
    assert first.status_code == 200
    assert first.json()["version"] == 1 and first.json()["statements"] == 3

    finalized = client.get(f"/api/v1/billing/statements?period={PERIOD_PARAM}&finalized=true", headers=headers).json()
    assert [s["totals"] for s in finalized] == [s["totals"] for s in sorted(live, key=lambda s: s["customer_number"] or "")]

    # A later price change alters the live calculation, not the finalized record.
    fee = next(f for f in billing_terms.list_fee_items(db_session, tenant.id) if f.code == "PP-MAINT")
    client.patch(f"/api/v1/billing/fee-items/{fee.id}", headers=headers, json={"unit_price": "99.00"})
    again = client.get(f"/api/v1/billing/statements?period={PERIOD_PARAM}&finalized=true", headers=headers).json()
    assert [s["totals"] for s in again] == [s["totals"] for s in finalized]

    second = client.post(f"/api/v1/billing/periods/{PERIOD_PARAM}/finalize", headers=headers).json()
    assert second["version"] == 2
    status = client.get(f"/api/v1/billing/periods/{PERIOD_PARAM}", headers=headers).json()
    assert [(v["version"], v["current"]) for v in status["versions"]] == [(2, True), (1, False)]
    assert Decimal(status["net_total"]) > Decimal(first.json()["net_total"])

    db_session.expire_all()
    assert db_session.query(AuditLogEntry).filter(AuditLogEntry.action == "billing.period.finalize").count() == 2


def test_exports_need_a_finalized_period(client, db_session, tenant_factory):
    *_, headers = _setup(client, db_session, tenant_factory, "billing-noexport")
    for name in ("analysis-feed.csv", "hard-charge-postings.csv", "camt086.xml"):
        assert client.get(f"/api/v1/billing/periods/{PERIOD_PARAM}/{name}", headers=headers).status_code == 409


def test_analysis_feed_and_hard_charge_postings(client, db_session, tenant_factory):
    tenant, _users, analysis, hard, accounts, headers = _setup(client, db_session, tenant_factory, "billing-feeds")
    client.post(f"/api/v1/billing/periods/{PERIOD_PARAM}/finalize", headers=headers)

    feed = client.get(f"/api/v1/billing/periods/{PERIOD_PARAM}/analysis-feed.csv", headers=headers)
    rows = list(csv.DictReader(io.StringIO(feed.text)))
    assert {r["customer_number"] for r in rows} == {"A-1"}  # analysis customers only
    assert {r["account_number"] for r in rows} == {"A-100"}  # at the billing account
    assert {r["payment_method"] for r in rows} == {"BCMP"}
    item = next(r for r in rows if r["fee_code"] == "PP-ITEM")
    assert (item["afp_code"], item["volume"], item["charge_amount"], item["statement_status"]) == ("150120", "5", "0.50", "ORGN")

    postings = client.get(f"/api/v1/billing/periods/{PERIOD_PARAM}/hard-charge-postings.csv", headers=headers)
    rows = list(csv.DictReader(io.StringIO(postings.text)))
    assert len(rows) == 1
    assert (rows[0]["customer_number"], rows[0]["account_number"], rows[0]["amount"]) == ("H-1", "H-100", "25.10")
    assert rows[0]["description"].startswith("Positive pay services ")


def test_camt086_statement(client, db_session, tenant_factory):
    *_, headers = _setup(client, db_session, tenant_factory, "billing-camt")
    client.post(f"/api/v1/billing/periods/{PERIOD_PARAM}/finalize", headers=headers)

    resp = client.get(f"/api/v1/billing/periods/{PERIOD_PARAM}/camt086.xml", headers=headers)
    assert resp.status_code == 200 and resp.headers["content-type"].startswith("application/xml")
    root = ET.fromstring(resp.content)
    assert root.tag == "{urn:iso:std:iso:20022:tech:xsd:camt.086.001.04}Document"
    groups = root.findall("c:BkSvcsBllgStmt/c:BllgStmtGrp", NS)
    assert len(groups) == 3  # two customers and the bank-wide statement

    by_receiver = {g.find("c:Rcvr/c:Id/c:OrgId/c:Othr/c:Id", NS).text: g for g in groups}
    analysis = by_receiver["A-1"].find("c:BllgStmt", NS)
    assert analysis.find("c:Sts", NS).text == "ORGN"
    assert analysis.find("c:AcctChrtcs/c:CshAcct/c:Id/c:Othr/c:Id", NS).text == "A-100"
    assert analysis.find("c:AcctChrtcs/c:CompstnMtd", NS).text == "DBTD"
    service = next(s for s in analysis.findall("c:Svc", NS) if s.find("c:SvcDtl/c:BkSvc/c:Id", NS).text == "PP-ITEM")
    assert service.find("c:SvcDtl/c:BkSvc/c:CmonCd/c:Issr", NS).text == "AFP"
    assert service.find("c:SvcDtl/c:BkSvc/c:CmonCd/c:Id", NS).text == "150120"
    assert service.find("c:SvcDtl/c:Vol", NS).text == "5"
    assert service.find("c:Pric/c:UnitPric/c:Amt", NS).text == "0.1000"
    assert service.find("c:Pric/c:UnitPric/c:Amt", NS).attrib["Ccy"] == "USD"
    assert service.find("c:PmtMtd", NS).text == "BCMP"
    assert service.find("c:OrgnlChrgPric/c:Amt", NS).text == "0.50"
    assert service.find("c:TaxDsgnt/c:Cd", NS).text == "XMPT"

    hard = by_receiver["H-1"].find("c:BllgStmt", NS)
    assert {s.find("c:PmtMtd", NS).text for s in hard.findall("c:Svc", NS)} == {"FLAT"}
    bank = by_receiver[next(k for k in by_receiver if k not in ("A-1", "H-1"))].find("c:BllgStmt", NS)
    assert bank.find("c:AcctChrtcs/c:CompstnMtd", NS).text == "NOCP"  # the bank-wide statement is exempt
    assert {s.find("c:PmtMtd", NS).text for s in bank.findall("c:Svc", NS)} == {"WVED"}


def test_customer_users_see_their_finalized_statement_but_no_exports(client, db_session, tenant_factory):
    tenant, users, analysis, hard, accounts, headers = _setup(client, db_session, tenant_factory, "billing-cust-final")
    client.post(f"/api/v1/billing/periods/{PERIOD_PARAM}/finalize", headers=headers)
    group = security_group_service.create_security_group(
        db_session, tenant.id, security_group_service.SecurityGroupInput(name="Customer Billing", permissions=["billing:read"])
    )
    user_service.create_user_with_membership(
        db_session, tenant.id, email="final@customer.example.com", password=TenantFactory.PASSWORD,
        security_group_id=group.id, customer_id=hard.id,
    )
    db_session.commit()
    customer = login_headers(client, tenant.slug, "final@customer.example.com")

    mine = client.get(f"/api/v1/billing/statements?period={PERIOD_PARAM}&finalized=true", headers=customer).json()
    assert [s["customer_number"] for s in mine] == ["H-1"]
    assert client.get(f"/api/v1/billing/periods/{PERIOD_PARAM}/hard-charge-postings.csv", headers=customer).status_code == 403
    assert client.post(f"/api/v1/billing/periods/{PERIOD_PARAM}/finalize", headers=customer).status_code == 403
