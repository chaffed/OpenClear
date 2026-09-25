# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Tests for the X9.37 image cash letter parser (bulk_import/x937.py).

There is no real-world sample X9.37 file available in this repo to validate against, so
these tests build synthetic fixtures using the exact byte layout the parser itself
documents (imported directly from the module so a future offset change and its test stay
in lockstep) — this validates the parser's internal consistency and documented
conventions, not byte-for-byte fidelity against a real bank-produced file. See
bulk_import/x937.py's module docstring for the documented, lenient-subset scope."""

from decimal import Decimal

import pytest

from openclear.bulk_import.x937 import (
    _STANDARD_RECORD_LEN,
    _T10_BUSINESS_DATE,
    _T25_AMOUNT,
    _T25_AUX_ON_US,
    _T25_ON_US,
    _T25_ROUTING_NUMBER,
    _T50_LEN,
    _T52_HEADER_BEFORE_LENGTH_FIELD,
    _T52_LENGTH_FIELD_LEN,
    _T70_ITEMS_COUNT,
    _T70_TOTAL_AMOUNT,
    _T90_ITEMS_COUNT,
    _T90_TOTAL_AMOUNT,
    _T99_ITEMS_COUNT,
    _T99_TOTAL_AMOUNT,
    X937ParseError,
    parse_x937_file,
)


def _fixed_record(record_type: str, fields, length: int = _STANDARD_RECORD_LEN) -> bytes:
    # `fields` is (slice, value) pairs, not a dict keyed by slice: slices only became
    # hashable in Python 3.12, and 3.11 is supported (see pyproject.toml).
    buf = bytearray(b" " * length)
    buf[0:2] = record_type.encode("ascii")
    for field_slice, value in fields:
        encoded = value.encode("ascii")
        buf[field_slice] = encoded.ljust(field_slice.stop - field_slice.start)[: field_slice.stop - field_slice.start]
    return bytes(buf)


def _file_header() -> bytes:
    return _fixed_record("01", {})


def _cash_letter_header(business_date: str = "20260115") -> bytes:
    return _fixed_record("10", [(_T10_BUSINESS_DATE, business_date)])


def _bundle_header() -> bytes:
    return _fixed_record("20", {})


def _check_detail(*, routing: str, on_us: str, aux_on_us: str, amount_cents: int) -> bytes:
    return _fixed_record(
        "25",
        [
            (_T25_ROUTING_NUMBER, routing),
            (_T25_ON_US, on_us),
            (_T25_AUX_ON_US, aux_on_us),
            (_T25_AMOUNT, str(amount_cents).zfill(_T25_AMOUNT.stop - _T25_AMOUNT.start)),
        ],
    )


def _image_view_detail() -> bytes:
    return _fixed_record("50", {}, length=_T50_LEN)


def _image_view_data(image_bytes: bytes) -> bytes:
    header = bytearray(b" " * _T52_HEADER_BEFORE_LENGTH_FIELD)
    header[0:2] = b"52"
    length_field = str(len(image_bytes)).zfill(_T52_LENGTH_FIELD_LEN).encode("ascii")
    return bytes(header) + length_field + image_bytes


def _bundle_control(*, items: int = 1, amount_cents: int = 0) -> bytes:
    return _fixed_record(
        "70",
        [
            (_T70_ITEMS_COUNT, str(items).zfill(_T70_ITEMS_COUNT.stop - _T70_ITEMS_COUNT.start)),
            (_T70_TOTAL_AMOUNT, str(amount_cents).zfill(_T70_TOTAL_AMOUNT.stop - _T70_TOTAL_AMOUNT.start)),
        ],
    )


def _cash_letter_control(*, items: int = 1, amount_cents: int = 0) -> bytes:
    return _fixed_record(
        "90",
        [
            (_T90_ITEMS_COUNT, str(items).zfill(_T90_ITEMS_COUNT.stop - _T90_ITEMS_COUNT.start)),
            (_T90_TOTAL_AMOUNT, str(amount_cents).zfill(_T90_TOTAL_AMOUNT.stop - _T90_TOTAL_AMOUNT.start)),
        ],
    )


def _file_control(*, items: int = 1, amount_cents: int = 0) -> bytes:
    return _fixed_record(
        "99",
        [
            (_T99_ITEMS_COUNT, str(items).zfill(_T99_ITEMS_COUNT.stop - _T99_ITEMS_COUNT.start)),
            (_T99_TOTAL_AMOUNT, str(amount_cents).zfill(_T99_TOTAL_AMOUNT.stop - _T99_TOTAL_AMOUNT.start)),
        ],
    )


def test_parses_a_single_check_with_front_and_back_images():
    front = b"\xff\xd8fake-jpeg-front"
    back = b"\xff\xd8fake-jpeg-back"
    content = b"".join(
        [
            _file_header(),
            _cash_letter_header("20260115"),
            _bundle_header(),
            _check_detail(routing="12345678", on_us="1001", aux_on_us="5001", amount_cents=15000),
            _image_view_detail(),
            _image_view_data(front),
            _image_view_detail(),
            _image_view_data(back),
            _bundle_control(items=1, amount_cents=15000),
            _cash_letter_control(items=1, amount_cents=15000),
            _file_control(items=1, amount_cents=15000),
        ]
    )

    items = parse_x937_file(content)
    assert len(items) == 1
    item = items[0]
    assert item.routing_number == "12345678"
    assert item.account_number == "1001"
    assert item.check_number == "5001"
    assert item.amount == Decimal("150.00")
    assert item.presented_date.isoformat() == "2026-01-15"
    assert item.front_image_bytes == front
    assert item.back_image_bytes == back


def test_parses_multiple_checks_in_one_cash_letter():
    content = b"".join(
        [
            _file_header(),
            _cash_letter_header("20260115"),
            _bundle_header(),
            _check_detail(routing="12345678", on_us="1001", aux_on_us="5001", amount_cents=15000),
            _image_view_detail(),
            _image_view_data(b"front-1"),
            _check_detail(routing="12345678", on_us="1002", aux_on_us="5002", amount_cents=2599),
            _image_view_detail(),
            _image_view_data(b"front-2"),
            _bundle_control(items=2, amount_cents=17599),
            _cash_letter_control(items=2, amount_cents=17599),
            _file_control(items=2, amount_cents=17599),
        ]
    )

    items = parse_x937_file(content)
    assert [i.check_number for i in items] == ["5001", "5002"]
    assert items[1].amount == Decimal("25.99")
    assert items[1].front_image_bytes == b"front-2"
    assert items[1].back_image_bytes is None  # only one image pair -> no back


def test_check_with_no_image_pair_gets_empty_front_bytes():
    content = b"".join(
        [
            _file_header(),
            _cash_letter_header(),
            _bundle_header(),
            _check_detail(routing="12345678", on_us="1001", aux_on_us="5001", amount_cents=100),
            _bundle_control(items=1, amount_cents=100),
            _file_control(items=1, amount_cents=100),
        ]
    )
    items = parse_x937_file(content)
    assert items[0].front_image_bytes == b""
    assert items[0].back_image_bytes is None


def test_tolerates_crlf_between_records():
    content = b"\r\n".join(
        [
            _file_header(),
            _cash_letter_header(),
            _check_detail(routing="12345678", on_us="1001", aux_on_us="5001", amount_cents=100),
            _image_view_detail(),
            _image_view_data(b"front"),
            _file_control(items=1, amount_cents=100),
        ]
    )
    items = parse_x937_file(content)
    assert len(items) == 1
    assert items[0].front_image_bytes == b"front"


def test_unrecognized_record_type_raises():
    content = _file_header() + _fixed_record("77", {})
    with pytest.raises(X937ParseError, match="Unrecognized record type"):
        parse_x937_file(content)


def test_truncated_file_raises():
    content = _file_header()[:10]
    with pytest.raises(X937ParseError, match="Truncated"):
        parse_x937_file(content)


def test_non_numeric_amount_raises():
    record = bytearray(_check_detail(routing="12345678", on_us="1001", aux_on_us="5001", amount_cents=100))
    record[_T25_AMOUNT] = b"NOTANUMBER"
    content = _file_header() + bytes(record) + _file_control()
    with pytest.raises(X937ParseError, match="non-numeric amount"):
        parse_x937_file(content)


def test_image_length_exceeding_remaining_file_raises():
    header = bytearray(b" " * _T52_HEADER_BEFORE_LENGTH_FIELD)
    header[0:2] = b"52"
    length_field = str(999999).zfill(_T52_LENGTH_FIELD_LEN).encode("ascii")
    bad_image_record = bytes(header) + length_field + b"only-a-few-bytes"
    content = (
        _file_header()
        + _check_detail(routing="12345678", on_us="1001", aux_on_us="5001", amount_cents=100)
        + _image_view_detail()
        + bad_image_record
    )
    with pytest.raises(X937ParseError, match="doesn't fit in the remaining file"):
        parse_x937_file(content)


def test_blank_business_date_falls_back_to_today_without_raising():
    content = (
        _file_header()
        + _cash_letter_header("        ")
        + _check_detail(routing="12345678", on_us="1001", aux_on_us="5001", amount_cents=100)
        + _file_control(items=1, amount_cents=100)
    )
    items = parse_x937_file(content)
    assert items[0].presented_date is not None


def test_cash_letter_control_item_count_mismatch_raises():
    content = (
        _file_header()
        + _cash_letter_header()
        + _check_detail(routing="12345678", on_us="1001", aux_on_us="5001", amount_cents=100)
        + _cash_letter_control(items=2, amount_cents=100)
        + _file_control(items=1, amount_cents=100)
    )
    with pytest.raises(X937ParseError, match="items count"):
        parse_x937_file(content)


def test_cash_letter_control_total_amount_mismatch_raises():
    content = (
        _file_header()
        + _cash_letter_header()
        + _check_detail(routing="12345678", on_us="1001", aux_on_us="5001", amount_cents=100)
        + _cash_letter_control(items=1, amount_cents=999)
        + _file_control(items=1, amount_cents=100)
    )
    with pytest.raises(X937ParseError, match="total amount"):
        parse_x937_file(content)


def test_file_control_item_count_mismatch_raises():
    content = (
        _file_header()
        + _cash_letter_header()
        + _check_detail(routing="12345678", on_us="1001", aux_on_us="5001", amount_cents=100)
        + _file_control(items=2, amount_cents=100)
    )
    with pytest.raises(X937ParseError, match="items count"):
        parse_x937_file(content)


def test_file_control_total_amount_mismatch_raises():
    content = (
        _file_header()
        + _cash_letter_header()
        + _check_detail(routing="12345678", on_us="1001", aux_on_us="5001", amount_cents=100)
        + _file_control(items=1, amount_cents=999)
    )
    with pytest.raises(X937ParseError, match="total amount"):
        parse_x937_file(content)


def test_cash_letter_totals_reset_between_cash_letters():
    """Two cash letters in one file: each cash letter control validates only its own
    items, but the file control validates the sum across both."""
    content = (
        _file_header()
        + _cash_letter_header()
        + _check_detail(routing="12345678", on_us="1001", aux_on_us="5001", amount_cents=100)
        + _cash_letter_control(items=1, amount_cents=100)
        + _cash_letter_header()
        + _check_detail(routing="12345678", on_us="1002", aux_on_us="5002", amount_cents=200)
        + _cash_letter_control(items=1, amount_cents=200)
        + _file_control(items=2, amount_cents=300)
    )
    items = parse_x937_file(content)
    assert len(items) == 2


def test_bundle_control_item_count_mismatch_raises():
    content = (
        _file_header()
        + _cash_letter_header()
        + _bundle_header()
        + _check_detail(routing="12345678", on_us="1001", aux_on_us="5001", amount_cents=100)
        + _bundle_control(items=2, amount_cents=100)
        + _cash_letter_control(items=1, amount_cents=100)
        + _file_control(items=1, amount_cents=100)
    )
    with pytest.raises(X937ParseError, match="Bundle Control.*items count"):
        parse_x937_file(content)


def test_bundle_control_total_amount_mismatch_raises():
    content = (
        _file_header()
        + _cash_letter_header()
        + _bundle_header()
        + _check_detail(routing="12345678", on_us="1001", aux_on_us="5001", amount_cents=100)
        + _bundle_control(items=1, amount_cents=999)
        + _cash_letter_control(items=1, amount_cents=100)
        + _file_control(items=1, amount_cents=100)
    )
    with pytest.raises(X937ParseError, match="Bundle Control.*total amount"):
        parse_x937_file(content)


def test_blank_bundle_control_is_rejected():
    """A Bundle Control with its count fields left blank can't be checked, so the file is
    rejected rather than trusted."""
    content = (
        _file_header()
        + _cash_letter_header()
        + _bundle_header()
        + _check_detail(routing="12345678", on_us="1001", aux_on_us="5001", amount_cents=100)
        + _fixed_record("70", {})
        + _file_control(items=1, amount_cents=100)
    )
    with pytest.raises(X937ParseError):
        parse_x937_file(content)


def test_bundle_totals_reset_between_bundles():
    """Two bundles in one cash letter: each bundle control validates only its own items,
    the cash letter control validates the sum."""
    content = (
        _file_header()
        + _cash_letter_header()
        + _bundle_header()
        + _check_detail(routing="12345678", on_us="1001", aux_on_us="5001", amount_cents=100)
        + _bundle_control(items=1, amount_cents=100)
        + _bundle_header()
        + _check_detail(routing="12345678", on_us="1001", aux_on_us="5002", amount_cents=250)
        + _check_detail(routing="12345678", on_us="1001", aux_on_us="5003", amount_cents=50)
        + _bundle_control(items=2, amount_cents=300)
        + _cash_letter_control(items=3, amount_cents=400)
        + _file_control(items=3, amount_cents=400)
    )
    items = parse_x937_file(content)
    assert [i.check_number for i in items] == ["5001", "5002", "5003"]
