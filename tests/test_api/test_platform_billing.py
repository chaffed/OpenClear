# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""The platform operator billing banks (BILLING_PLAN.md B4)."""

import csv
import io
import xml.etree.ElementTree as ET

from openclear.services import platform_api_key_service
from tests.test_api.test_billing import PERIOD_PARAM, _bank_with_activity

NS = {"c": "urn:iso:std:iso:20022:tech:xsd:camt.086.001.04"}

from tests.conftest import approve_provider_changes


def _key(db_session, scopes=("billing",)):
    _row, raw = platform_api_key_service.generate_and_create(db_session, "Operator", scopes)
    db_session.commit()
    return {"X-Api-Key": raw}


def _schedule(client, headers, db_session=None):
    """The fees, each asked for over the API and approved by a platform operator (DUAL_CONTROL_PLAN.md DC4)."""
    for fee in (
        {"code": "BANK", "description": "Platform fee per bank", "metric": "bank_monthly", "unit_price": "500"},
        {"code": "ITEMS", "description": "Checks presented", "metric": "presented_checks", "unit_price": "0.02"},
        {"code": "ACCTS", "description": "Accounts", "metric": "account_monthly", "unit_price": "1.00"},
    ):
        resp = client.post("/api/v1/platform/billing/fee-items", headers=headers, json=fee)
        assert resp.status_code == 202, resp.text
    if db_session is not None:
        approve_provider_changes(db_session)


def test_invoices_total_each_banks_usage(client, db_session, tenant_factory):
    bank_a, *_ = _bank_with_activity(db_session, tenant_factory, "platform-bill-a")
    bank_b, *_ = _bank_with_activity(db_session, tenant_factory, "platform-bill-b")
    demo, *_ = _bank_with_activity(db_session, tenant_factory, "platform-bill-demo")
    demo.is_demo = True
    db_session.commit()
    headers = _key(db_session)
    _schedule(client, headers, db_session)

    invoices = client.get(f"/api/v1/platform/billing/invoices?period={PERIOD_PARAM}", headers=headers)
    assert invoices.status_code == 200, invoices.text
    by_bank = {i["bank_slug"]: i for i in invoices.json()}
    assert set(by_bank) >= {"platform-bill-a", "platform-bill-b"}
    assert "platform-bill-demo" not in by_bank  # the public demo is never invoiced

    lines = {line["fee_code"]: line for line in by_bank["platform-bill-a"]["lines"]}
    assert lines["BANK"]["volume"] == 1 and lines["BANK"]["net_amount"] == "500.00"
    assert lines["ITEMS"]["volume"] == 7  # every customer's checks plus the bank's own
    assert lines["ACCTS"]["volume"] == 4  # the bank's account and three customer accounts
    assert {line["payment_method"] for line in lines.values()} == {"FLAT"}  # always a hard charge
    assert by_bank["platform-bill-a"]["totals"]["net"] == "504.14"

    one = client.get(f"/api/v1/platform/billing/invoices?period={PERIOD_PARAM}&tenant_id={bank_b.id}", headers=headers).json()
    assert [i["bank_slug"] for i in one] == ["platform-bill-b"]

    report = client.get(f"/api/v1/platform/billing/invoices.csv?period={PERIOD_PARAM}", headers=headers)
    assert {r["customer_number"] for r in csv.DictReader(io.StringIO(report.text))} >= {"platform-bill-a", "platform-bill-b"}

    camt = client.get(f"/api/v1/platform/billing/invoices/camt086.xml?period={PERIOD_PARAM}&tenant_id={bank_a.id}", headers=headers)
    root = ET.fromstring(camt.content)
    group = root.find("c:BkSvcsBllgStmt/c:BllgStmtGrp", NS)
    assert group.find("c:Sndr/c:Nm", NS).text == "OpenClear platform operator"
    assert group.find("c:Rcvr/c:Id/c:OrgId/c:Othr/c:Id", NS).text == "platform-bill-a"


def test_platform_billing_needs_the_billing_scope(client, db_session):
    usage_only = _key(db_session, scopes=("usage",))
    assert client.get("/api/v1/platform/billing/fee-items", headers=usage_only).status_code == 403
    assert client.get(f"/api/v1/platform/billing/invoices?period={PERIOD_PARAM}", headers=usage_only).status_code == 403


def test_platform_fee_validation(client, db_session):
    headers = _key(db_session)
    assert client.post("/api/v1/platform/billing/fee-items", headers=headers, json={"code": "x", "description": "x", "metric": "nope"}).status_code == 422
    first = client.post("/api/v1/platform/billing/fee-items", headers=headers, json={"code": "dup", "description": "x", "metric": "bank_monthly"})
    assert first.status_code == 202
    approve_provider_changes(db_session)
    assert client.post("/api/v1/platform/billing/fee-items", headers=headers, json={"code": "DUP", "description": "x", "metric": "bank_monthly"}).status_code == 422
