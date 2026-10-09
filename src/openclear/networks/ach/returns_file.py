# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""A NACHA return file for the ACH debits a bank decided to return (SDK_PLAN.md P3): the
`nacha_returns` decision layout. The bank, as RDFI, sends it to its ACH operator.

Each returned debit becomes a return entry (transaction code one less than the original's:
27 → 26 for checking, 37 → 36 for savings) with a type 99 return addenda carrying the R code
and the original trace number, grouped into one batch per original company, SEC code and
effective date. The ODFI is read from the original trace number's first eight digits. Files
are checked against Moov's ACH validator in the tests (tests/test_networks/test_ach_returns_file.py).

Settings (per bank, Settings → Decision files): the bank's routing number (immediate origin and
RDFI), its name, and the ACH operator's routing number and name (immediate destination)."""

from __future__ import annotations

from collections import OrderedDict
from datetime import date, datetime, timezone
from decimal import Decimal

SETTINGS_FIELDS = (
    ("routing_number", "Your routing number (9 digits)"),
    ("bank_name", "Your bank's name (as on file headers)"),
    ("operator_routing_number", "ACH operator's routing number (9 digits), e.g. 091000080 for the Federal Reserve"),
    ("operator_name", "ACH operator's name, e.g. FED"),
)


class ReturnsFileError(ValueError):
    pass


def check_digit(routing8: str) -> str:
    weights = (3, 7, 1, 3, 7, 1, 3, 7)
    total = sum(int(d) * w for d, w in zip(routing8, weights))
    return str((10 - total % 10) % 10)


def _routing(value: str | None, what: str) -> str:
    digits = "".join(ch for ch in (value or "") if ch.isdigit())
    if len(digits) != 9 or check_digit(digits[:8]) != digits[8]:
        raise ReturnsFileError(f"The {what} must be a valid 9-digit routing number.")
    return digits


def _alpha(value: str | None, width: int) -> str:
    text = "".join(ch if 32 <= ord(ch) < 127 else " " for ch in (value or "")).upper()
    return text[:width].ljust(width)


def _num(value: int, width: int) -> str:
    text = str(int(value))
    if len(text) > width:
        raise ReturnsFileError(f"{value} doesn't fit in {width} digits.")
    return text.zfill(width)


def _cents(amount) -> int:
    return int((Decimal(str(amount)) * 100).to_integral_value())


def _return_code(entry: dict) -> str:
    code = (entry.get("return_transaction_code") or "").strip().upper()
    if len(code) == 3 and code[0] == "R" and code[1:].isdigit():
        return code
    raise ReturnsFileError(f"Decision {entry.get('sequence')} has no R code to return with (got {code or 'none'!r}).")


def write(feed: list[dict], settings: dict[str, str], *, now: datetime | None = None) -> bytes:
    """The return file for the ACH returns among `feed` (decision feed entries; checks and
    paid items are skipped). Empty if there are none."""
    returns = [e for e in feed if e.get("network") == "ach" and e.get("outcome") == "return"]
    if not returns:
        return b""
    rdfi = _routing(settings.get("routing_number"), "bank's routing number")
    operator = _routing(settings.get("operator_routing_number"), "ACH operator's routing number")
    now = now or datetime.now(timezone.utc)
    today = now.date()

    batches: OrderedDict[tuple, list[dict]] = OrderedDict()
    for entry in sorted(returns, key=lambda e: e["sequence"]):
        key = (entry.get("originator_id") or "", (entry.get("originator_name") or ""), entry.get("sec_code") or "PPD",
               entry.get("effective_date") or today.isoformat())
        batches.setdefault(key, []).append(entry)

    lines = [
        "1" + "01" + " " + operator + " " + rdfi + now.strftime("%y%m%d%H%M") + "A" + "094" + "10" + "1"
        + _alpha(settings.get("operator_name") or "ACH OPERATOR", 23) + _alpha(settings.get("bank_name"), 23) + _alpha("RETURNS", 8)
    ]
    trace_sequence = 0
    entry_count = addenda_count = 0
    file_hash = 0
    file_debit = file_credit = 0
    for batch_number, ((company_id, company_name, sec, effective), entries) in enumerate(batches.items(), start=1):
        effective_date = date.fromisoformat(effective) if isinstance(effective, str) else effective
        lines.append(
            "5" + "225" + _alpha(company_name, 16) + _alpha("", 20) + _alpha(company_id, 10) + _alpha(sec, 3)
            + _alpha("RETURN", 10) + _alpha("", 6) + effective_date.strftime("%y%m%d") + "   " + "1" + rdfi[:8] + _num(batch_number, 7)
        )
        batch_hash = batch_debit = batch_credit = 0
        for entry in entries:
            trace = "".join(ch for ch in (entry.get("trace_number") or "") if ch.isdigit())
            if len(trace) != 15:
                raise ReturnsFileError(f"Decision {entry.get('sequence')}'s trace number isn't 15 digits.")
            odfi8 = trace[:8]
            code = _return_code(entry)
            savings = (entry.get("account_type") or "").lower() == "savings"
            transaction_code = "36" if savings else "26"  # the return of a 37/27 debit
            cents = _cents(entry["amount"])
            trace_sequence += 1
            return_trace = rdfi[:8] + _num(trace_sequence, 7)
            lines.append(
                "6" + transaction_code + odfi8 + check_digit(odfi8) + _alpha(entry.get("account_number"), 17) + _num(cents, 10)
                + _alpha(entry.get("receiver_id") or "", 15) + _alpha(entry.get("receiver_name") or entry.get("account_name") or "ACCOUNT HOLDER", 22)
                + "  " + "1" + return_trace
            )
            lines.append(
                "7" + "99" + code + trace + " " * 6 + rdfi[:8] + _alpha(f"OPENCLEAR {entry.get('decision_id', '')}"[:44], 44) + return_trace
            )
            batch_hash += int(odfi8)
            batch_debit += cents  # 26/36 ("return of a debit") count as debit entries
            entry_count += 1
            addenda_count += 1
        count = len(entries) * 2
        lines.append(
            "8" + "225" + _num(count, 6) + _num(batch_hash % 10**10, 10) + _num(batch_debit, 12) + _num(batch_credit, 12)
            + _alpha(company_id, 10) + " " * 19 + " " * 6 + rdfi[:8] + _num(batch_number, 7)
        )
        file_hash += batch_hash
        file_debit += batch_debit
        file_credit += batch_credit

    records = len(lines) + 1
    blocks = -(-records // 10)
    lines.append(
        "9" + _num(len(batches), 6) + _num(blocks, 6) + _num(entry_count + addenda_count, 8) + _num(file_hash % 10**10, 10)
        + _num(file_debit, 12) + _num(file_credit, 12) + " " * 39
    )
    lines += ["9" * 94] * (blocks * 10 - records)
    for line in lines:
        assert len(line) == 94, (len(line), line)
    return ("\n".join(lines) + "\n").encode("ascii")
