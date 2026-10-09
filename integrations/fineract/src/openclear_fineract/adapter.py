# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 Chaffed

"""A reference core adapter for Apache Fineract (SDK_PLAN.md P3), built on the OpenClear SDK.

Fineract has no check clearing of its own, but a savings withdrawal records how it was paid
(`paymentDetailData`: payment type, check number, routing code). So, run on a schedule:

1. **sync**: Fineract clients become OpenClear customers (customer number: the client's
   external id, or its account number) and savings accounts become accounts (number: the
   account number), upserted by those numbers.
2. **presented**: new withdrawals paid by check (payment type in `check_payment_types`, with
   a check number) are sent to OpenClear as presented items (paid items), which match them
   against the issued-check file and raise exceptions. The adapter remembers which Fineract
   transaction each one came from.
3. **decisions**: for every new decision in the feed, a returned check's withdrawal is
   undone in Fineract (the money goes back to the account); a paid one needs nothing. Each
   handled decision is acknowledged as consumer `fineract`.

State (what's been sent, and the transaction for each check) is a small SQLite file, so a
run can stop and resume. Everything Fineract and OpenClear do is idempotent by key, so a
repeated run does no harm."""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from openclear_sdk import OpenClear

from openclear_fineract.fineract import Fineract

logger = logging.getLogger(__name__)


@dataclass
class Settings:
    check_payment_types: tuple[str, ...] = ("Check", "Cheque")
    consumer: str = "fineract"


@dataclass
class Report:
    customers: int = 0
    accounts: int = 0
    presented: int = 0
    returned: int = 0
    paid: int = 0
    skipped: list[str] = field(default_factory=list)


def _date(value) -> str:
    """Fineract dates are [yyyy, m, d] lists (or ISO strings in newer releases)."""
    if isinstance(value, list) and len(value) == 3:
        return date(*value).isoformat()
    return str(value)[:10]


class State:
    def __init__(self, path: str | Path) -> None:
        self.db = sqlite3.connect(str(path))
        self.db.execute("CREATE TABLE IF NOT EXISTS sent (fineract_tx INTEGER PRIMARY KEY, account_id INTEGER, account_number TEXT, check_number TEXT, amount TEXT)")
        self.db.commit()

    def was_sent(self, tx_id: int) -> bool:
        return self.db.execute("SELECT 1 FROM sent WHERE fineract_tx = ?", (tx_id,)).fetchone() is not None

    def record(self, tx_id: int, account_id: int, account_number: str, check_number: str, amount: str) -> None:
        self.db.execute("INSERT OR IGNORE INTO sent VALUES (?, ?, ?, ?, ?)", (tx_id, account_id, account_number, check_number, amount))
        self.db.commit()

    def find(self, account_number: str, check_number: str) -> tuple[int, int] | None:
        row = self.db.execute(
            "SELECT account_id, fineract_tx FROM sent WHERE account_number = ? AND check_number = ? ORDER BY fineract_tx DESC",
            (account_number, check_number),
        ).fetchone()
        return (row[0], row[1]) if row else None


class Adapter:
    def __init__(self, openclear: OpenClear, fineract: Fineract, state: State, settings: Settings | None = None) -> None:
        self.oc, self.fineract, self.state = openclear, fineract, state
        self.settings = settings or Settings()

    def sync(self, report: Report | None = None) -> Report:
        report = report or Report()
        numbers: dict[int, str] = {}
        for client in self.fineract.clients():
            number = str(client.get("externalId") or client.get("accountNo") or client["id"])
            numbers[client["id"]] = number
            self.oc.customers.upsert_customer(number, {
                "name": client.get("displayName") or number, "is_active": bool(client.get("active", True)),
                "external_customer_id": str(client["id"]),
            })
            report.customers += 1
        for account in self.fineract.savings_accounts():
            body = {"name": account.get("savingsProductName") or "Savings", "external_account_id": str(account["id"])}
            if account.get("clientId") in numbers:
                body["customer_number"] = numbers[account["clientId"]]
            self.oc.accounts.upsert_account(str(account["accountNo"]), body)
            report.accounts += 1
        return report

    def presented(self, report: Report | None = None) -> Report:
        report = report or Report()
        items, sources = [], []
        accounts = {a["account_number"]: a for a in self.oc.accounts.iter_accounts()}
        for account in self.fineract.savings_accounts():
            local = accounts.get(str(account["accountNo"]))
            if local is None:
                report.skipped.append(f"account {account['accountNo']} isn't synced yet")
                continue
            for tx in self.fineract.savings_transactions(account["id"]):
                kind = (tx.get("transactionType") or {})
                if not kind.get("withdrawal") or tx.get("reversed") or self.state.was_sent(tx["id"]):
                    continue
                payment = tx.get("paymentDetailData") or {}
                if (payment.get("paymentType") or {}).get("name") not in self.settings.check_payment_types or not payment.get("checkNumber"):
                    continue
                items.append({"account_id": local["id"], "check_number": str(payment["checkNumber"]),
                              "presented_amount": str(tx["amount"]), "presented_date": _date(tx["date"])})
                sources.append((tx["id"], account["id"], str(account["accountNo"]), str(payment["checkNumber"]), str(tx["amount"])))
        if items:
            result = self.oc.bulk.submit("paid_items", items)
            for row in result.results:
                if row.get("success"):
                    self.state.record(*sources[row["index"]])
                    report.presented += 1
                else:
                    report.skipped.append(f"check {sources[row['index']][3]}: {row.get('error')}")
        return report

    def decisions(self, report: Report | None = None) -> Report:
        report = report or Report()

        def handle(decision: dict) -> None:
            if decision.get("network") != "check":
                return
            if decision.get("outcome") != "return":
                report.paid += 1
                return
            found = self.state.find(decision.get("account_number") or "", decision.get("check_number") or "")
            if found is None:
                report.skipped.append(f"decision {decision['sequence']}: no Fineract transaction for check {decision.get('check_number')}")
                return
            self.fineract.undo_transaction(*found)
            report.returned += 1

        self.oc.decision_feed.consume(self.settings.consumer, handle)
        return report

    def run(self) -> Report:
        report = Report()
        self.sync(report)
        self.presented(report)
        self.decisions(report)
        return report
