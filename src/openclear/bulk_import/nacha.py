# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Lenient NACHA (ACH file format) parser.

Extracts only what's needed to create ach_transaction rows: batch header fields
(company id/name, SEC code, effective date) and entry detail fields (DFI account
number, amount, individual id, transaction code, trace number). Batch control (type 8)
and file control (type 9) records ARE now validated (entry/addenda count, entry hash,
total debit/credit dollar amounts) against what was actually parsed — see
_validate_batch_control/_validate_file_control below — but file header (record type 1)
and addenda (type 7) records are still not validated; a genuinely malformed file can
still partially "parse" past those. A file with no file control record at all only
skips file-level validation — batch-level validation still applies to every batch
control record present.

Field positions below are 1-indexed per the NACHA spec, standard 94-character records.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation


class NachaParseError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class NachaEntry:
    line_number: int
    dfi_account_number: str
    amount: Decimal
    individual_id: str | None  # -> ach receiver_id
    transaction_code: str
    trace_number: str
    company_id: str  # -> ach originator_id, from the enclosing batch header
    company_name: str  # -> ach originator_name
    sec_code: str
    effective_date: date


# Transaction Code (entry detail, positions 2-3) -> debit/credit. Checking (2x) and
# savings (3x) accounts each have credit/prenote-credit/zero-dollar-credit and the debit
# equivalents; prenotes and zero-dollar entries still move real money in our model, so
# they're mapped the same as their non-prenote counterpart rather than treated specially.
_CREDIT_CODES = {"22", "23", "24", "32", "33", "34"}
_DEBIT_CODES = {"27", "28", "29", "37", "38", "39"}

_ENTRY_HASH_MODULUS = 10**10  # NACHA entry hash is the low-order 10 digits of the sum


@dataclass
class _RunningTotals:
    entry_count: int = 0
    entry_hash_sum: int = 0
    total_debit_cents: int = 0
    total_credit_cents: int = 0

    def add_entry(self, *, receiving_dfi_id: str, transaction_code: str, amount_cents: int) -> None:
        self.entry_count += 1
        self.entry_hash_sum += int(receiving_dfi_id or "0")
        if transaction_code in _DEBIT_CODES:
            self.total_debit_cents += amount_cents
        elif transaction_code in _CREDIT_CODES:
            self.total_credit_cents += amount_cents


def transaction_type_for_code(transaction_code: str) -> str:
    if transaction_code in _CREDIT_CODES:
        return "credit"
    if transaction_code in _DEBIT_CODES:
        return "debit"
    raise NachaParseError(f"Unrecognized or unsupported transaction code {transaction_code!r}")


def _parse_declared_int(raw: str, *, field_name: str, line_number: int) -> int:
    stripped = raw.strip()
    try:
        return int(stripped) if stripped else 0
    except ValueError:
        raise NachaParseError(f"Line {line_number}: non-numeric {field_name} field {raw!r}") from None


def _compare(
    *, field_name: str, declared: int, actual: int, line_number: int, scope: str
) -> None:
    if declared != actual:
        raise NachaParseError(
            f"Line {line_number}: {scope} control record's {field_name} ({declared}) "
            f"doesn't match what was actually parsed ({actual})"
        )


def _validate_batch_control(line: str, totals: _RunningTotals, *, line_number: int) -> None:
    declared_count = _parse_declared_int(line[4:10], field_name="entry/addenda count", line_number=line_number)
    declared_hash = _parse_declared_int(line[10:20], field_name="entry hash", line_number=line_number)
    declared_debit = _parse_declared_int(line[20:32], field_name="total debit amount", line_number=line_number)
    declared_credit = _parse_declared_int(line[32:44], field_name="total credit amount", line_number=line_number)

    _compare(field_name="entry/addenda count", declared=declared_count, actual=totals.entry_count, line_number=line_number, scope="batch")
    _compare(
        field_name="entry hash", declared=declared_hash, actual=totals.entry_hash_sum % _ENTRY_HASH_MODULUS,
        line_number=line_number, scope="batch",
    )
    _compare(field_name="total debit amount", declared=declared_debit, actual=totals.total_debit_cents, line_number=line_number, scope="batch")
    _compare(field_name="total credit amount", declared=declared_credit, actual=totals.total_credit_cents, line_number=line_number, scope="batch")


def _validate_file_control(line: str, totals: _RunningTotals, *, line_number: int) -> None:
    declared_count = _parse_declared_int(line[13:21], field_name="entry/addenda count", line_number=line_number)
    declared_hash = _parse_declared_int(line[21:31], field_name="entry hash", line_number=line_number)
    declared_debit = _parse_declared_int(line[31:43], field_name="total debit amount", line_number=line_number)
    declared_credit = _parse_declared_int(line[43:55], field_name="total credit amount", line_number=line_number)

    _compare(field_name="entry/addenda count", declared=declared_count, actual=totals.entry_count, line_number=line_number, scope="file")
    _compare(
        field_name="entry hash", declared=declared_hash, actual=totals.entry_hash_sum % _ENTRY_HASH_MODULUS,
        line_number=line_number, scope="file",
    )
    _compare(field_name="total debit amount", declared=declared_debit, actual=totals.total_debit_cents, line_number=line_number, scope="file")
    _compare(field_name="total credit amount", declared=declared_credit, actual=totals.total_credit_cents, line_number=line_number, scope="file")


def parse_nacha_file(content: bytes) -> list[NachaEntry]:
    try:
        text = content.decode("ascii")
    except UnicodeDecodeError:
        text = content.decode("ascii", errors="replace")

    entries: list[NachaEntry] = []
    current_batch: dict | None = None
    batch_totals = _RunningTotals()
    file_totals = _RunningTotals()

    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.rstrip("\n\r")
        if not line.strip():
            continue
        if len(line) < 94:
            line = line.ljust(94)  # tolerate short/unpadded trailing records

        record_type = line[0]

        if record_type == "5":  # Batch Header
            current_batch = {
                "company_name": line[4:20].strip(),
                "company_id": line[40:50].strip(),
                "sec_code": line[50:53].strip(),
                "effective_date": _parse_yymmdd(line[69:75].strip(), line_number),
            }
            batch_totals = _RunningTotals()

        elif record_type == "6":  # Entry Detail
            if current_batch is None:
                raise NachaParseError(f"Line {line_number}: entry detail record appears before any batch header")

            transaction_code = line[1:3]
            receiving_dfi_id = line[3:11].strip()  # only used for the batch/file entry-hash check below
            dfi_account_number = line[12:29].strip()
            amount_cents = line[29:39].strip()
            individual_id = line[39:54].strip() or None
            trace_number = line[79:94].strip()

            if not dfi_account_number:
                raise NachaParseError(f"Line {line_number}: entry detail record has no DFI account number")
            try:
                amount_cents_int = int(amount_cents)
                amount = Decimal(amount_cents_int) / Decimal(100)
            except (InvalidOperation, ValueError):
                raise NachaParseError(f"Line {line_number}: invalid amount field {amount_cents!r}") from None

            batch_totals.add_entry(receiving_dfi_id=receiving_dfi_id, transaction_code=transaction_code, amount_cents=amount_cents_int)
            file_totals.add_entry(receiving_dfi_id=receiving_dfi_id, transaction_code=transaction_code, amount_cents=amount_cents_int)

            entries.append(
                NachaEntry(
                    line_number=line_number,
                    dfi_account_number=dfi_account_number,
                    amount=amount,
                    individual_id=individual_id,
                    transaction_code=transaction_code,
                    trace_number=trace_number,
                    company_id=current_batch["company_id"],
                    company_name=current_batch["company_name"],
                    sec_code=current_batch["sec_code"],
                    effective_date=current_batch["effective_date"],
                )
            )

        elif record_type == "8":  # Batch Control
            _validate_batch_control(line, batch_totals, line_number=line_number)
            current_batch = None  # closes the batch; an entry detail after this without a new '5' is an error

        elif record_type == "9":  # File Control
            _validate_file_control(line, file_totals, line_number=line_number)

        # Record types 1 (file header) and 7 (addenda) are intentionally ignored in
        # lenient mode.

    if not entries:
        raise NachaParseError("No entry detail records (type '6') found in this file")

    return entries


def _parse_yymmdd(raw: str, line_number: int) -> date:
    if len(raw) != 6 or not raw.isdigit():
        raise NachaParseError(f"Line {line_number}: invalid effective date field {raw!r}")
    yy, mm, dd = int(raw[0:2]), int(raw[2:4]), int(raw[4:6])
    year = 2000 + yy if yy < 80 else 1900 + yy  # standard NACHA 2-digit-year pivot
    try:
        return date(year, mm, dd)
    except ValueError:
        raise NachaParseError(f"Line {line_number}: invalid effective date {raw!r}") from None
