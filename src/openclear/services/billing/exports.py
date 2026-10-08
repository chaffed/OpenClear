# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""The billing exports (BILLING_PLAN.md B2), produced from a finalized period:

- the **account analysis feed** (CSV): every service line of customers billed by account
  analysis, at their billing account, for the bank's analysis system to offset with
  earnings credit (lines are BCMP, or WVED/FREE);
- the **hard-charge postings** (CSV): one debit per hard-charge customer with something
  due, for the core's fee-posting import (camt.086 compensation method DBTD);
- **camt.086** (ISO 20022 Bank Services Billing statement, XML): every statement, with the
  AFP code in CmonCd. Follows the ISO message definition (camt.086.001.04 structure). The
  namespace version is a setting; validate against your counterparty's XSD before relying
  on it in production. Taxes aren't calculated: a taxable fee is marked TAXE with no
  amounts.
"""

import csv
import io
import xml.etree.ElementTree as ET
from decimal import Decimal

from openclear.domain.billing import BillingMethod
from openclear.services.billing.reports import _csv_safe
from openclear.services.billing.statements import Statement

CAMT086_DEFAULT_VERSION = "camt.086.001.04"
CURRENCY = "USD"

ANALYSIS_FEED_COLUMNS = (
    "period_start", "period_end", "customer_number", "customer_name", "account_number", "afp_code",
    "bank_service_code", "fee_code", "description", "volume", "unit_price", "charge_method", "list_amount",
    "discount_amount", "charge_amount", "payment_method", "statement_status",
)
POSTING_COLUMNS = (
    "period_start", "period_end", "customer_number", "customer_name", "account_number", "amount", "description",
    "statement_id", "statement_status",
)


def _write(columns, rows) -> str:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=columns)
    writer.writeheader()
    for row in rows:
        writer.writerow({key: _csv_safe(value) for key, value in row.items()})
    return buffer.getvalue()


def analysis_feed_csv(statements: list[Statement]) -> str:
    rows = []
    for s in statements:
        if s.billing_method != BillingMethod.ANALYSIS:
            continue
        for line in s.lines:
            p = line.priced
            rows.append({
                "period_start": s.period.start.isoformat(), "period_end": s.period.end.isoformat(),
                "customer_number": s.customer_number or "", "customer_name": s.customer_name,
                "account_number": s.billing_account_number or "", "afp_code": line.afp_code or "",
                "bank_service_code": line.bank_service_code or "", "fee_code": line.code, "description": line.description,
                "volume": p.volume, "unit_price": str(p.unit_price), "charge_method": p.charge_method,
                "list_amount": str(p.list_amount), "discount_amount": str(p.discount_amount),
                "charge_amount": str(p.net_amount), "payment_method": p.payment_method, "statement_status": s.status,
            })
    return _write(ANALYSIS_FEED_COLUMNS, rows)


def _month_label(s: Statement) -> str:
    if s.period.start.day == 1 and (s.period.end.month != s.period.start.month or s.period.end.day >= 28):
        return s.period.start.strftime("%b %Y")
    return f"{s.period.start.isoformat()} to {s.period.end.isoformat()}"


def postings_csv(statements: list[Statement], *, description_prefix: str = "Positive pay services") -> str:
    rows = [
        {
            "period_start": s.period.start.isoformat(), "period_end": s.period.end.isoformat(),
            "customer_number": s.customer_number or "", "customer_name": s.customer_name,
            "account_number": s.billing_account_number or "", "amount": str(s.net_total),
            "description": f"{description_prefix} {_month_label(s)}", "statement_id": str(s.record_id or ""),
            "statement_status": s.status,
        }
        for s in statements
        if s.billing_method == BillingMethod.HARD_CHARGE and s.net_total > 0
    ]
    return _write(POSTING_COLUMNS, rows)


def _sub(parent, tag: str, text=None, **attrs):
    element = ET.SubElement(parent, tag, attrs)
    if text is not None:
        element.text = str(text)
    return element


def _amount(parent, tag: str, value: Decimal, places: str = "0.01"):
    element = _sub(parent, tag)
    _sub(element, "Amt", str(abs(value).quantize(Decimal(places))), Ccy=CURRENCY)
    _sub(element, "Sgn", "true" if value >= 0 else "false")


def _party(parent, tag: str, name: str, identifier: str):
    party = _sub(parent, tag)
    _sub(party, "Nm", name[:140])
    other = _sub(_sub(_sub(party, "Id"), "OrgId"), "Othr")
    _sub(other, "Id", identifier[:35])


def camt086_xml(statements: list[Statement], *, bank_name: str, bank_id: str, report_id: str, version: str = CAMT086_DEFAULT_VERSION) -> bytes:
    namespace = f"urn:iso:std:iso:20022:tech:xsd:{version}"
    document = ET.Element("Document", xmlns=namespace)
    root = _sub(document, "BkSvcsBllgStmt")
    _sub(_sub(root, "RptHdr"), "RptId", report_id[:35])

    for s in statements:
        group = _sub(root, "BllgStmtGrp")
        _sub(group, "GrpId", f"{report_id}-{s.customer_number or 'BANK'}"[:35])
        _party(group, "Sndr", bank_name, bank_id)
        _party(group, "Rcvr", s.customer_name, s.customer_number or bank_id)

        statement = _sub(group, "BllgStmt")
        _sub(statement, "StmtId", str(s.record_id or report_id)[:35])
        period = _sub(statement, "FrToDt")
        _sub(period, "FrDt", s.period.start.isoformat())
        _sub(period, "ToDt", s.period.end.isoformat())
        created = s.finalized_at.isoformat(timespec="seconds") if hasattr(s.finalized_at, "isoformat") else None
        _sub(statement, "CreDtTm", created or "")
        _sub(statement, "Sts", "TEST" if s.status == "DRAFT" else s.status)

        account = _sub(statement, "AcctChrtcs")
        _sub(account, "AcctLvl", "DETL")
        _sub(_sub(_sub(_sub(account, "CshAcct"), "Id"), "Othr"), "Id", (s.billing_account_number or "UNASSIGNED")[:34])
        _sub(account, "CompstnMtd", "NOCP" if s.billing_method == BillingMethod.EXEMPT else "DBTD")
        _sub(account, "AcctBalCcyCd", CURRENCY)
        _sub(_sub(account, "AcctSvcrCtct"), "Nm", bank_name[:140])

        for line in s.lines:
            p = line.priced
            service = _sub(statement, "Svc")
            detail = _sub(service, "SvcDtl")
            bank_service = _sub(detail, "BkSvc")
            _sub(bank_service, "Id", (line.bank_service_code or line.code)[:35])
            _sub(bank_service, "Desc", line.description[:70])
            if line.afp_code:
                common = _sub(bank_service, "CmonCd")
                _sub(common, "Issr", "AFP")
                _sub(common, "Id", line.afp_code)
            _sub(detail, "Vol", p.volume)
            price = _sub(service, "Pric")
            _sub(price, "Ccy", CURRENCY)
            _amount(price, "UnitPric", p.unit_price, places="0.0001")
            _sub(price, "Mtd", p.charge_method)
            _sub(service, "PmtMtd", p.payment_method)
            # The calculated charge before any waiver (camt.086 asks for the waived amount
            # here on WVED lines); the amount actually due follows from PmtMtd.
            _amount(service, "OrgnlChrgPric", p.list_amount - p.discount_amount)
            _sub(_sub(service, "TaxDsgnt"), "Cd", "TAXE" if line.taxable else "XMPT")

    ET.indent(document)
    return ET.tostring(document, encoding="utf-8", xml_declaration=True)
