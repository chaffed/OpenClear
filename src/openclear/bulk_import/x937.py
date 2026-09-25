# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Lenient parser for X9.37 ("DSTU X9.37" / Check 21) image cash letter files.

Supports the common ASCII, line-delimited "variable format" convention: every record's
own length is fixed (or, for Image View Data, computed from a length field within the
record itself), and each record may optionally be followed by a CR and/or LF separator,
which is tolerated but not required — this parser advances by each record's own known
length regardless, and only skips over CR/LF bytes if present immediately afterward.

Extracts only what OpenClear needs to create a paid_item + check_image per check: from each
Check Detail Record (Type 25), the Payor Bank Routing Number, the On-Us field (->
account_number) and Auxiliary On-Us field (-> check_number) — the common real-world MICR
layout, where the check's own serial number sits in Auxiliary On-Us and the payor's
account number in On-Us. This is a convention, not a standard-mandated split (X9.37
doesn't define a universal way to divide account number from check number within a bank's
own on-us MICR data), so a cash letter whose issuing bank uses a different layout won't
resolve correctly here — the ZIP+CSV bulk path is the explicit-column fallback for that
case. Front/back images are taken positionally from the Image View Detail (Type 50) /
Image View Data (Type 52) record pairs following each Check Detail Record: the first pair
is the front, the second the back (X9.37 doesn't mark a side indicator OpenClear relies on;
side is inferred from order, matching the standard duplex-scan convention).

Deliberately NOT supported, matching this app's existing NACHA parser's "lenient,
documented subset" philosophy (bulk_import/nacha.py): the alternate fixed-length
undelimited binary X9.37 variant, EBCDIC-encoded files, return/adjustment cash letters,
and credit reconcilement records.

Control totals ARE validated against what was actually parsed, at every level: Bundle
Control (Type 70) against the checks since the preceding Bundle Header (Type 20), Cash
Letter Control (Type 90) against the cash letter, and File Control (Type 99) against the
whole file — item count and total dollar amount each. A mismatch means the file was
truncated, altered, or assembled wrongly, so the whole file is rejected rather than
loading a partial or inconsistent set of checks. Header records (types 01, 20) are
otherwise only read far enough to skip over them, apart from the Cash Letter Header
(Type 10), whose Business Date is read so bulk-loaded items carry a real presented_date
instead of always defaulting to today.

The control-record field positions follow the commonly published X9.37 (DSTU X9.37-2003)
layouts, but this repo has no real bank-produced cash-letter file to verify byte-for-byte
against (the test suite builds synthetic files from these same constants). X9.37 layouts
vary more across real-world implementations than NACHA's rigid 94-character format: if a
real file from your processor is rejected on a control-total mismatch that looks wrong,
check these positions first.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation


class X937ParseError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class X937CheckItem:
    item_number: int  # 1-based position among check detail records in this file, for error messages
    routing_number: str
    account_number: str  # from the On-Us field -- may be blank if the source file leaves it unencoded
    check_number: str  # from the Auxiliary On-Us field -- may be blank, same caveat
    amount: Decimal
    presented_date: date  # from the enclosing Cash Letter Header's Business Date, or today if unparseable
    front_image_bytes: bytes  # empty if no Image View Data record followed this check's detail record
    back_image_bytes: bytes | None


_STANDARD_RECORD_LEN = 80  # every record type below except 50/52 is fixed-length at 80 bytes

# Type 25 - Check Detail Record (80 bytes). Byte positions (0-indexed slices) follow the
# record's commonly published field layout; OpenClear only needs a handful of its fields.
_T25_AUX_ON_US = slice(2, 17)  # Auxiliary On-Us (15) -> check_number, by convention (see module docstring)
_T25_ROUTING_NUMBER = slice(18, 26)  # Payor Bank Routing Number (8)
_T25_ON_US = slice(27, 47)  # On-Us (20) -> account_number, by convention
_T25_AMOUNT = slice(47, 57)  # Item Amount (10 digits, unsigned, implied 2 decimal places)

# Type 10 - Cash Letter Header Record (80 bytes). Only the Business Date is read; every
# other field (destination/origin routing, creation date/time, ...) is skipped.
_T10_BUSINESS_DATE = slice(21, 29)  # Cash Letter Business Date (8, YYYYMMDD)

# Type 50 - Image View Detail Record: fixed length. Not parsed for field content --
# nothing in it is needed for OpenClear's data model, only its length matters so the scanner
# can advance to the following Image View Data (Type 52) record.
_T50_LEN = 200

# Type 52 - Image View Data Record: a header of unparsed fields (institution/sequence/
# security/view-descriptor data OpenClear doesn't need) followed by a 10-digit "Length of
# Image Data" field, then that many raw image bytes. Some processors append their own
# trailer fields (e.g. a digital signature) after the image data, which this parser does
# not expect -- if present, the extra bytes are simply not part of what's returned as
# image data, and image decoding downstream (bulk_import/images.py) will surface a clean,
# isolated error for that one item rather than misreading the rest of the file, since the
# declared length -- not a guess -- is what determines exactly how many bytes are image.
_T52_HEADER_BEFORE_LENGTH_FIELD = 116
_T52_LENGTH_FIELD_LEN = 10
_T52_HEADER_LEN = _T52_HEADER_BEFORE_LENGTH_FIELD + _T52_LENGTH_FIELD_LEN

# Type 90 - Cash Letter Control Record (80 bytes). Only Items Count and Total Amount are
# read; everything else (bundle count, imaged items count, ...) is skipped. See the
# module docstring for the "own best-effort convention, not verified against a real
# file" caveat.
_T90_ITEMS_COUNT = slice(8, 16)
_T90_TOTAL_AMOUNT = slice(16, 30)

# Type 99 - File Control Record (80 bytes). Same two fields, file-wide instead of
# per-cash-letter.
_T99_ITEMS_COUNT = slice(16, 24)
_T99_TOTAL_AMOUNT = slice(24, 39)

# Type 70 - Bundle Control Record (80 bytes): Items Within Bundle Count (4, positions 3-6)
# and Bundle Total Amount (12, positions 7-18, implied 2 decimal places).
_T70_ITEMS_COUNT = slice(2, 6)
_T70_TOTAL_AMOUNT = slice(6, 18)

_SKIP_RECORD_TYPES = {"01", "26", "27", "28", "31", "32", "33", "34"}


def _parse_business_date(raw: str) -> date:
    try:
        return date(int(raw[0:4]), int(raw[4:6]), int(raw[6:8]))
    except (ValueError, IndexError):
        return date.today()


def _skip_separators(content: bytes, cursor: int) -> int:
    while cursor < len(content) and content[cursor] in (0x0D, 0x0A):
        cursor += 1
    return cursor


def _parse_declared_int(raw: bytes, *, field_name: str, cursor: int) -> int:
    text = raw.decode("ascii", errors="replace").strip()
    try:
        return int(text) if text else 0
    except ValueError:
        raise X937ParseError(f"Control record at byte {cursor} has a non-numeric {field_name} field: {text!r}") from None


def _compare_control_total(*, field_name: str, declared: int, actual: int, cursor: int, scope: str) -> None:
    if declared != actual:
        raise X937ParseError(
            f"{scope} Control record at byte {cursor}: declared {field_name} ({declared}) doesn't match "
            f"what was actually parsed ({actual})"
        )


def parse_x937_file(content: bytes) -> list[X937CheckItem]:
    """Scans an X9.37 file front to back, returning one X937CheckItem per Check Detail
    Record (Type 25) encountered, with its following image pair (if any) attached.
    Raises X937ParseError for anything that indicates the file doesn't match the
    supported layout (unrecognized record type, a truncated record, or a Type 52 whose
    declared image length doesn't fit in the remaining file) -- these are treated as
    whole-file structural problems, not a single bad check, since there's no reliable way
    to resynchronize the scanner past a record whose type or length can't be trusted."""
    items: list[X937CheckItem] = []
    cursor = 0
    item_number = 0

    current_routing = ""
    current_account = ""
    current_check_number = ""
    current_amount: Decimal | None = None
    current_images: list[bytes] = []
    current_business_date = date.today()

    bundle_items = 0
    bundle_amount_cents = 0
    cash_letter_items = 0
    cash_letter_amount_cents = 0
    file_items = 0
    file_amount_cents = 0

    def _flush_current() -> None:
        if current_amount is None:
            return
        items.append(
            X937CheckItem(
                item_number=item_number,
                routing_number=current_routing,
                account_number=current_account,
                check_number=current_check_number,
                amount=current_amount,
                presented_date=current_business_date,
                front_image_bytes=current_images[0] if len(current_images) >= 1 else b"",
                back_image_bytes=current_images[1] if len(current_images) >= 2 else None,
            )
        )

    while cursor < len(content):
        remaining = len(content) - cursor
        if remaining < 2:
            raise X937ParseError(f"Truncated record at byte {cursor} (not enough bytes for a record type)")
        record_type = content[cursor : cursor + 2].decode("ascii", errors="replace")

        if record_type == "25":
            if remaining < _STANDARD_RECORD_LEN:
                raise X937ParseError(f"Truncated Check Detail Record (Type 25) at byte {cursor}")
            record = content[cursor : cursor + _STANDARD_RECORD_LEN]

            _flush_current()
            item_number += 1
            current_images = []
            current_routing = record[_T25_ROUTING_NUMBER].decode("ascii", errors="replace").strip()
            current_account = record[_T25_ON_US].decode("ascii", errors="replace").strip()
            current_check_number = record[_T25_AUX_ON_US].decode("ascii", errors="replace").strip()
            amount_raw = record[_T25_AMOUNT].decode("ascii", errors="replace").strip()
            try:
                amount_cents = int(amount_raw or "0")
                current_amount = Decimal(amount_cents) / Decimal(100)
            except (InvalidOperation, ValueError):
                raise X937ParseError(
                    f"Check Detail Record #{item_number} at byte {cursor} has a non-numeric amount field: {amount_raw!r}"
                ) from None
            bundle_items += 1
            bundle_amount_cents += amount_cents
            cash_letter_items += 1
            cash_letter_amount_cents += amount_cents
            file_items += 1
            file_amount_cents += amount_cents

            cursor = _skip_separators(content, cursor + _STANDARD_RECORD_LEN)

        elif record_type == "10":
            if remaining < _STANDARD_RECORD_LEN:
                raise X937ParseError(f"Truncated Cash Letter Header Record (Type 10) at byte {cursor}")
            record = content[cursor : cursor + _STANDARD_RECORD_LEN]
            current_business_date = _parse_business_date(record[_T10_BUSINESS_DATE].decode("ascii", errors="replace").strip())
            cash_letter_items = 0
            cash_letter_amount_cents = 0
            cursor = _skip_separators(content, cursor + _STANDARD_RECORD_LEN)

        elif record_type == "20":
            if remaining < _STANDARD_RECORD_LEN:
                raise X937ParseError(f"Truncated Bundle Header Record (Type 20) at byte {cursor}")
            bundle_items = 0
            bundle_amount_cents = 0
            cursor = _skip_separators(content, cursor + _STANDARD_RECORD_LEN)

        elif record_type == "70":
            if remaining < _STANDARD_RECORD_LEN:
                raise X937ParseError(f"Truncated Bundle Control Record (Type 70) at byte {cursor}")
            record = content[cursor : cursor + _STANDARD_RECORD_LEN]
            declared_items = _parse_declared_int(record[_T70_ITEMS_COUNT], field_name="items count", cursor=cursor)
            declared_amount = _parse_declared_int(record[_T70_TOTAL_AMOUNT], field_name="total amount", cursor=cursor)
            _compare_control_total(field_name="items count", declared=declared_items, actual=bundle_items, cursor=cursor, scope="Bundle")
            _compare_control_total(field_name="total amount", declared=declared_amount, actual=bundle_amount_cents, cursor=cursor, scope="Bundle")
            cursor = _skip_separators(content, cursor + _STANDARD_RECORD_LEN)

        elif record_type == "90":
            if remaining < _STANDARD_RECORD_LEN:
                raise X937ParseError(f"Truncated Cash Letter Control Record (Type 90) at byte {cursor}")
            record = content[cursor : cursor + _STANDARD_RECORD_LEN]
            declared_items = _parse_declared_int(record[_T90_ITEMS_COUNT], field_name="items count", cursor=cursor)
            declared_amount = _parse_declared_int(record[_T90_TOTAL_AMOUNT], field_name="total amount", cursor=cursor)
            _compare_control_total(field_name="items count", declared=declared_items, actual=cash_letter_items, cursor=cursor, scope="Cash Letter")
            _compare_control_total(field_name="total amount", declared=declared_amount, actual=cash_letter_amount_cents, cursor=cursor, scope="Cash Letter")
            cursor = _skip_separators(content, cursor + _STANDARD_RECORD_LEN)

        elif record_type == "99":
            if remaining < _STANDARD_RECORD_LEN:
                raise X937ParseError(f"Truncated File Control Record (Type 99) at byte {cursor}")
            record = content[cursor : cursor + _STANDARD_RECORD_LEN]
            declared_items = _parse_declared_int(record[_T99_ITEMS_COUNT], field_name="items count", cursor=cursor)
            declared_amount = _parse_declared_int(record[_T99_TOTAL_AMOUNT], field_name="total amount", cursor=cursor)
            _compare_control_total(field_name="items count", declared=declared_items, actual=file_items, cursor=cursor, scope="File")
            _compare_control_total(field_name="total amount", declared=declared_amount, actual=file_amount_cents, cursor=cursor, scope="File")
            cursor = _skip_separators(content, cursor + _STANDARD_RECORD_LEN)

        elif record_type == "50":
            if remaining < _T50_LEN:
                raise X937ParseError(f"Truncated Image View Detail Record (Type 50) at byte {cursor}")
            cursor = _skip_separators(content, cursor + _T50_LEN)

        elif record_type == "52":
            if remaining < _T52_HEADER_LEN:
                raise X937ParseError(f"Truncated Image View Data Record (Type 52) header at byte {cursor}")
            length_field = content[
                cursor + _T52_HEADER_BEFORE_LENGTH_FIELD : cursor + _T52_HEADER_LEN
            ].decode("ascii", errors="replace").strip()
            try:
                image_length = int(length_field)
            except ValueError:
                raise X937ParseError(
                    f"Image View Data Record (Type 52) at byte {cursor} has a non-numeric image length field: {length_field!r}"
                ) from None
            if image_length < 0 or cursor + _T52_HEADER_LEN + image_length > len(content):
                raise X937ParseError(
                    f"Image View Data Record (Type 52) at byte {cursor} declares an image length ({image_length}) "
                    "that doesn't fit in the remaining file"
                )
            image_bytes = content[cursor + _T52_HEADER_LEN : cursor + _T52_HEADER_LEN + image_length]
            current_images.append(image_bytes)
            cursor = _skip_separators(content, cursor + _T52_HEADER_LEN + image_length)

        elif record_type in _SKIP_RECORD_TYPES:
            if remaining < _STANDARD_RECORD_LEN:
                raise X937ParseError(f"Truncated record type {record_type!r} at byte {cursor}")
            cursor = _skip_separators(content, cursor + _STANDARD_RECORD_LEN)

        else:
            raise X937ParseError(f"Unrecognized record type {record_type!r} at byte {cursor}")

    _flush_current()
    return items
