# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import io

import pytest

from openclear.bulk_import.tabular import TabularParseError, parse_tabular_file


def test_parses_comma_delimited_csv():
    content = b"account_number,check_number,amount\n1001,5001,150.00\n1001,5002,99.99\n"
    rows = parse_tabular_file("items.csv", content)

    assert len(rows) == 2
    assert rows[0]["account_number"] == "1001"
    assert rows[0]["check_number"] == "5001"
    assert rows[0]["amount"] == "150.00"


def test_auto_detects_pipe_delimiter():
    content = b"account_number|check_number|amount\n1001|5001|150.00\n"
    rows = parse_tabular_file("items.txt", content)
    assert rows[0]["check_number"] == "5001"


def test_auto_detects_tab_delimiter():
    content = b"account_number\tcheck_number\tamount\n1001\t5001\t150.00\n"
    rows = parse_tabular_file("items.tsv", content)
    assert rows[0]["check_number"] == "5001"


def test_normalizes_header_names():
    content = b"Account Number,Check   Number,Amount\n1001,5001,150.00\n"
    rows = parse_tabular_file("items.csv", content)
    assert rows[0]["account_number"] == "1001"
    assert rows[0]["check_number"] == "5001"


def test_preserves_leading_zeros_in_check_number():
    content = b"account_number,check_number,amount\n1001,00042,150.00\n"
    rows = parse_tabular_file("items.csv", content)
    assert rows[0]["check_number"] == "00042"  # would become int 42 without dtype=str


def test_empty_file_returns_empty_list():
    content = b"account_number,check_number,amount\n"
    rows = parse_tabular_file("items.csv", content)
    assert rows == []


def test_malformed_excel_raises_tabular_parse_error():
    with pytest.raises(TabularParseError):
        parse_tabular_file("items.xlsx", b"this is not a real xlsx file")


def test_parses_xlsx_file():
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.append(["account_number", "check_number", "amount", "payee_name", "issue_date"])
    ws.append(["1001", "5001", 150.00, "Acme Vendor", "2026-01-15"])

    buffer = io.BytesIO()
    wb.save(buffer)

    rows = parse_tabular_file("items.xlsx", buffer.getvalue())
    assert len(rows) == 1
    assert rows[0]["account_number"] == "1001"
    assert rows[0]["check_number"] == "5001"
    assert rows[0]["payee_name"] == "Acme Vendor"
