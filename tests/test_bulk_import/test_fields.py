# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

from datetime import date
from decimal import Decimal

import pytest

from openclear.bulk_import.fields import RowFieldError, optional_str, parse_date, parse_decimal, require_str


def test_require_str_returns_stripped_value():
    assert require_str({"foo": "  bar  "}, "foo") == "bar"


def test_require_str_missing_raises():
    with pytest.raises(RowFieldError, match="is required"):
        require_str({}, "foo")


def test_require_str_treats_nan_as_missing():
    with pytest.raises(RowFieldError, match="is required"):
        require_str({"foo": float("nan")}, "foo")


def test_optional_str_returns_none_when_absent():
    assert optional_str({}, "foo") is None
    assert optional_str({"foo": None}, "foo") is None


def test_optional_str_returns_value_when_present():
    assert optional_str({"foo": " bar "}, "foo") == "bar"


def test_parse_decimal_handles_dollar_sign_and_commas():
    assert parse_decimal({"amount": "$1,250.00"}, "amount") == Decimal("1250.00")


def test_parse_decimal_invalid_raises():
    with pytest.raises(RowFieldError, match="contains a letter") as error:
        parse_decimal({"amount": "abc"}, "amount")
    assert error.value.field == "amount"
    with pytest.raises(RowFieldError, match="isn't a valid amount"):
        parse_decimal({"amount": "1.2.3"}, "amount")


def test_parse_date_iso_format():
    assert parse_date({"d": "2026-01-15"}, "d") == date(2026, 1, 15)


def test_parse_date_us_format():
    assert parse_date({"d": "01/15/2026"}, "d") == date(2026, 1, 15)


def test_parse_date_strips_time_component_from_excel_timestamp():
    assert parse_date({"d": "2026-01-15 00:00:00"}, "d") == date(2026, 1, 15)


def test_parse_date_invalid_raises():
    with pytest.raises(RowFieldError, match="not a recognizable date"):
        parse_date({"d": "not a date"}, "d")
