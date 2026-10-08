# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Billing statements as data and as a CSV report (BILLING_PLAN.md). Shared by the API
and the web UI. Amounts are strings with two decimal places, so nothing is lost to floats."""

import csv
import io

from openclear.services.billing.statements import Statement

# Same spreadsheet-formula-injection guard as the other CSV exports (web/routers/users.py):
# customer names and descriptions are free text.
_FORMULA_LEADING_CHARS = ("=", "+", "-", "@")


def _csv_safe(value: object) -> object:
    if isinstance(value, str) and value.startswith(_FORMULA_LEADING_CHARS):
        return f"'{value}"
    return value


def statement_to_dict(statement: Statement) -> dict:
    return {
        "period_start": statement.period.start.isoformat(),
        "period_end": statement.period.end.isoformat(),
        "customer_id": str(statement.customer_id) if statement.customer_id else None,
        "customer_number": statement.customer_number,
        "customer_name": statement.customer_name,
        "billing_method": statement.billing_method.value,
        "billing_account_number": statement.billing_account_number,
        "totals": {
            "list": str(statement.list_total),
            "discount": str(statement.discount_total),
            "waived": str(statement.waived_total),
            "net": str(statement.net_total),
        },
        "lines": [
            {
                "fee_code": line.code,
                "description": line.description,
                "metric": line.metric,
                "unit": line.unit,
                "afp_code": line.afp_code,
                "bank_service_code": line.bank_service_code,
                "volume": line.priced.volume,
                "unit_price": str(line.priced.unit_price),
                "charge_method": line.priced.charge_method,
                "list_amount": str(line.priced.list_amount),
                "discount_amount": str(line.priced.discount_amount),
                "net_amount": str(line.priced.net_amount),
                "payment_method": line.priced.payment_method,
                "tiers": line.priced.tiers,
                "accounts": line.accounts,
            }
            for line in statement.lines
        ],
    }


REPORT_COLUMNS = (
    "period_start", "period_end", "customer_number", "customer_name", "billing_method", "billing_account",
    "fee_code", "description", "afp_code", "bank_service_code", "volume", "unit_price", "charge_method",
    "list_amount", "discount_amount", "net_amount", "payment_method",
)


def statements_to_csv(statements: list[Statement]) -> str:
    """One row per statement line: the period's billing report."""
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=REPORT_COLUMNS)
    writer.writeheader()
    for statement in statements:
        for line in statement.lines:
            writer.writerow({key: _csv_safe(value) for key, value in {
                "period_start": statement.period.start.isoformat(),
                "period_end": statement.period.end.isoformat(),
                "customer_number": statement.customer_number or "",
                "customer_name": statement.customer_name,
                "billing_method": statement.billing_method.value,
                "billing_account": statement.billing_account_number or "",
                "fee_code": line.code,
                "description": line.description,
                "afp_code": line.afp_code or "",
                "bank_service_code": line.bank_service_code or "",
                "volume": line.priced.volume,
                "unit_price": str(line.priced.unit_price),
                "charge_method": line.priced.charge_method,
                "list_amount": str(line.priced.list_amount),
                "discount_amount": str(line.priced.discount_amount),
                "net_amount": str(line.priced.net_amount),
                "payment_method": line.priced.payment_method,
            }.items()})
    return buffer.getvalue()
