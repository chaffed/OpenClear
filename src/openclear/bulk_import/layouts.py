# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Reading an issued-check file by a layout (UX_PLAN.md U1; domain/import_layout.py).

A layout's settings say how the file is laid out and where each field is:

- `file_type`: "delimited" (any one-character delimiter, with or without a header row),
  "fixed" (each field a start position and length, counted from 1), or "excel" (a sheet).
- `columns` (delimited, Excel): field → {"name": header} or {"position": n, from 1}.
- `positions` (fixed): field → {"start": n, "length": n}.
- `skip_rows` and `skip_footer`: lines to leave out at the top (after any header) and the
  bottom, such as a file header and trailer. `line_prefix` (fixed): only lines starting
  with it are items, such as "D" for detail records.
- `amount_format`: "decimal" ("1,234.56", "$1234.5") or "implied" ("000123456" is
  1,234.56); a separate `amount_cents` column adds cents to whole dollars.
- `date_format`: a name from DATE_FORMATS, such as "MMDDYY".
- `void_values`: what in the void column marks a void, comma-separated ("V, Y").
- `strip_zeros`: fields whose leading zeros are dropped ("check_number").

Reading a file gives each item's raw text per field (`extract`); `convert` turns one into
what the issued-item import expects, raising RowFieldError naming the field at fault.
Values are always trimmed."""

import csv
import io
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from openclear.bulk_import.fields import RowFieldError

FIELDS: dict[str, str] = {
    "account_number": "Account number",
    "check_number": "Check number",
    "amount": "Amount",
    "amount_cents": "Cents (separate column)",
    "issue_date": "Issue date",
    "payee_name": "Payee",
    "void": "Void flag",
}
REQUIRED = ("account_number", "check_number", "amount", "issue_date", "payee_name")
FILE_TYPES = {"delimited": "Delimited (CSV, tab, pipe…)", "fixed": "Fixed-width text", "excel": "Excel"}
AMOUNT_FORMATS = {"decimal": "With a decimal point (1,234.56)", "implied": "Implied cents (000123456 is 1,234.56)"}
DATE_FORMATS: dict[str, str | None] = {
    "auto": None,  # ISO, or the US forms the standard upload accepts
    "YYYY-MM-DD": "%Y-%m-%d",
    "MM/DD/YYYY": "%m/%d/%Y",
    "MM/DD/YY": "%m/%d/%y",
    "M/D/YYYY": "%m/%d/%Y",
    "DD/MM/YYYY": "%d/%m/%Y",
    "MMDDYY": "%m%d%y",
    "MMDDYYYY": "%m%d%Y",
    "YYMMDD": "%y%m%d",
    "YYYYMMDD": "%Y%m%d",
}
DELIMITERS = {",": "Comma", "\t": "Tab", "|": "Pipe", ";": "Semicolon"}
MAX_SAMPLE_ROWS = 15


class LayoutError(ValueError):
    """The layout itself is wrong, or the file can't be read by it; fit to show."""


@dataclass(frozen=True)
class Template:
    name: str
    description: str
    settings: dict


# Starting points for a new layout, not any one vendor's specification: pick the nearest,
# then match it to the file with a sample.
TEMPLATES: dict[str, Template] = {
    "standard_csv": Template(
        "CSV with column names", "A header row naming each column; amounts like 1,234.56.",
        {"file_type": "delimited", "delimiter": ",", "has_header": True,
         "columns": {"account_number": {"name": "Account"}, "check_number": {"name": "Check Number"}, "amount": {"name": "Amount"},
                     "issue_date": {"name": "Issue Date"}, "payee_name": {"name": "Payee"}},
         "amount_format": "decimal", "date_format": "auto"}),
    "headerless_csv": Template(
        "CSV without a header", "Account, check number, amount, date (MM/DD/YYYY) and payee, in that order.",
        {"file_type": "delimited", "delimiter": ",", "has_header": False,
         "columns": {"account_number": {"position": 1}, "check_number": {"position": 2}, "amount": {"position": 3},
                     "issue_date": {"position": 4}, "payee_name": {"position": 5}},
         "amount_format": "decimal", "date_format": "MM/DD/YYYY"}),
    "fixed_width": Template(
        "Fixed-width", "One item per line: account (12), check (10), amount in cents (12), date MMDDYYYY (8), "
        "void flag (1), payee (to 80).",
        {"file_type": "fixed", "positions": {"account_number": {"start": 1, "length": 12}, "check_number": {"start": 13, "length": 10},
                                              "amount": {"start": 23, "length": 12}, "issue_date": {"start": 35, "length": 8},
                                              "void": {"start": 43, "length": 1}, "payee_name": {"start": 44, "length": 37}},
         "amount_format": "implied", "date_format": "MMDDYYYY", "void_values": "V", "strip_zeros": ["check_number"]}),
    "excel": Template(
        "Excel with column names", "The first sheet, with a header row naming each column.",
        {"file_type": "excel", "has_header": True,
         "columns": {"account_number": {"name": "Account"}, "check_number": {"name": "Check Number"}, "amount": {"name": "Amount"},
                     "issue_date": {"name": "Issue Date"}, "payee_name": {"name": "Payee"}},
         "amount_format": "decimal", "date_format": "auto"}),
}


# --- Settings ----------------------------------------------------------------------------------


def _int(value: Any, what: str, minimum: int = 0) -> int:
    try:
        number = int(str(value).strip() or 0)
    except ValueError:
        raise LayoutError(f"{what} must be a whole number.") from None
    if number < minimum:
        raise LayoutError(f"{what} must be at least {minimum}.")
    return number


def clean_settings(raw: dict, *, complete: bool = True) -> dict:
    """The settings, checked and in their stored form. Raises LayoutError. With complete
    False (the builder, before every field is placed), missing fields are allowed."""
    file_type = raw.get("file_type")
    if file_type not in FILE_TYPES:
        raise LayoutError("Choose the kind of file.")
    settings: dict[str, Any] = {"file_type": file_type}
    if raw.get("amount_format", "decimal") not in AMOUNT_FORMATS:
        raise LayoutError("Choose how amounts are written.")
    if raw.get("date_format", "auto") not in DATE_FORMATS:
        raise LayoutError("Choose how dates are written.")
    settings["amount_format"] = raw.get("amount_format", "decimal")
    settings["date_format"] = raw.get("date_format", "auto")
    settings["skip_rows"] = _int(raw.get("skip_rows", 0), "Lines to skip at the top")
    settings["skip_footer"] = _int(raw.get("skip_footer", 0), "Lines to skip at the bottom")
    settings["void_values"] = ", ".join(v.strip() for v in str(raw.get("void_values") or "").split(",") if v.strip())
    settings["strip_zeros"] = [f for f in raw.get("strip_zeros") or [] if f in ("account_number", "check_number")]

    if file_type == "fixed":
        settings["line_prefix"] = str(raw.get("line_prefix") or "")
        positions = {}
        for field, spot in (raw.get("positions") or {}).items():
            if field not in FIELDS or not spot or not str(spot.get("start", "")).strip():
                continue
            positions[field] = {"start": _int(spot["start"], f"{FIELDS[field]}'s start", 1),
                                "length": _int(spot.get("length", 0), f"{FIELDS[field]}'s length", 1)}
        settings["positions"] = positions
        mapped = positions
    else:
        settings["has_header"] = bool(raw.get("has_header"))
        if file_type == "delimited":
            delimiter = raw.get("delimiter") or ","
            delimiter = "\t" if delimiter in ("tab", "\\t") else delimiter
            if len(delimiter) != 1:
                raise LayoutError("The delimiter is one character, such as a comma or a pipe.")
            settings["delimiter"] = delimiter
        else:
            settings["sheet"] = str(raw.get("sheet") or "").strip()
        columns = {}
        for field, ref in (raw.get("columns") or {}).items():
            if field not in FIELDS or not ref:
                continue
            if ref.get("name"):
                if not settings["has_header"]:
                    raise LayoutError(f"{FIELDS[field]} names a column, but the file has no header row; use its position.")
                columns[field] = {"name": str(ref["name"]).strip()}
            elif str(ref.get("position", "")).strip():
                columns[field] = {"position": _int(ref["position"], f"{FIELDS[field]}'s column", 1)}
        settings["columns"] = columns
        mapped = columns
    missing = [FIELDS[f] for f in REQUIRED if f not in mapped]
    if missing and complete:
        raise LayoutError("Say where to find: " + ", ".join(missing) + ".")
    if "void" in mapped and not settings["void_values"] and complete:
        raise LayoutError("Say what in the void column marks a void, such as V.")
    return settings


# --- Reading a file ------------------------------------------------------------------------------


def _text(content: bytes) -> str:
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    return content.decode("latin-1")


def _lines(content: bytes) -> list[str]:
    return [line for line in _text(content).splitlines() if line.strip()]


def _excel_rows(content: bytes, sheet: str) -> list[list[str]]:
    import pandas as pd

    try:
        frame = pd.read_excel(io.BytesIO(content), sheet_name=sheet or 0, header=None, dtype=str)
    except Exception as exc:  # noqa: BLE001 -- pandas raises many kinds for a bad workbook
        raise LayoutError(f"Couldn't read that workbook{f' (sheet {sheet!r})' if sheet else ''}: {exc}") from None
    frame = frame.where(frame.notna(), "")
    rows = [[str(cell) for cell in row] for row in frame.values.tolist()]
    return [row for row in rows if any(cell.strip() for cell in row)]


def _trim(rows: list, settings: dict) -> list:
    return rows[settings.get("skip_rows", 0):max(0, len(rows) - settings.get("skip_footer", 0))]


def grid(settings: dict, filename: str, content: bytes) -> tuple[list[str] | None, list[list[str]] | list[str]]:
    """The file as the layout sees it: (header, rows of cells) for delimited and Excel, or
    (None, item lines) for fixed-width. Skipped lines are already left out."""
    file_type = settings["file_type"]
    if file_type == "fixed":
        lines = _lines(content)
        lines = _trim(lines, settings)
        prefix = settings.get("line_prefix") or ""
        return None, [line for line in lines if line.startswith(prefix)]
    if file_type == "excel":
        rows = _excel_rows(content, settings.get("sheet", ""))
    else:
        rows = [row for row in csv.reader(io.StringIO(_text(content)), delimiter=settings.get("delimiter", ",")) if any(c.strip() for c in row)]
    header = None
    if settings.get("has_header") and rows:
        header, rows = [c.strip() for c in rows[0]], rows[1:]
    rows = _trim(rows, settings)
    return header, rows


def _column_index(header: list[str] | None, ref: dict, field: str) -> int:
    if "position" in ref:
        return ref["position"] - 1
    wanted = ref["name"].strip().lower()
    for index, name in enumerate(header or []):
        if name.strip().lower() == wanted:
            return index
    raise LayoutError(f"The file has no column named {ref['name']!r} ({FIELDS[field]}).")


def extract(settings: dict, filename: str, content: bytes) -> list[tuple[str, dict[str, str]]]:
    """Each item: (a label for its place in the file, field → raw text)."""
    header, rows = grid(settings, filename, content)
    first_line = 1 + (1 if header is not None else 0) + settings.get("skip_rows", 0)
    items = []
    if settings["file_type"] == "fixed":
        for number, line in enumerate(rows):
            values = {field: line[spot["start"] - 1: spot["start"] - 1 + spot["length"]].strip()
                      for field, spot in settings["positions"].items()}
            items.append((f"line {first_line + number}", values))
        return items
    indexes = {field: _column_index(header, ref, field) for field, ref in settings["columns"].items()}
    for number, row in enumerate(rows):
        values = {field: (row[index].strip() if index < len(row) else "") for field, index in indexes.items()}
        items.append((f"row {first_line + number}", values))
    return items


# --- One item ------------------------------------------------------------------------------------


def _amount(settings: dict, values: dict[str, str]) -> Decimal:
    raw = values.get("amount", "")
    if not raw:
        raise RowFieldError("Amount is missing", "amount")
    text = raw.replace("$", "").replace(",", "").replace(" ", "")
    negative = text.startswith("(") and text.endswith(")")
    text = text.strip("()")
    if any(ch.isalpha() for ch in text):
        raise RowFieldError(f"Amount {raw!r} contains a letter", "amount")
    try:
        if settings.get("amount_format") == "implied":
            if not text.lstrip("-").isdigit():
                raise RowFieldError(f"Amount {raw!r} should be digits only (implied cents)", "amount")
            amount = Decimal(int(text)) / 100
        else:
            amount = Decimal(text)
    except InvalidOperation:
        raise RowFieldError(f"Amount {raw!r} isn't a number", "amount") from None
    if "amount_cents" in values:
        cents = values["amount_cents"] or "0"
        if not cents.isdigit() or len(cents) > 2:
            raise RowFieldError(f"Cents {values['amount_cents']!r} should be 0 to 99", "amount_cents")
        amount = amount.to_integral_value() + Decimal(int(cents)) / 100
    if negative:
        amount = -amount
    if amount <= 0:
        raise RowFieldError(f"Amount {raw!r} must be more than zero", "amount")
    return amount.quantize(Decimal("0.01"))


def _date(settings: dict, raw: str) -> date:
    if not raw:
        raise RowFieldError("Issue date is missing", "issue_date")
    fmt_name = settings.get("date_format", "auto")
    fmt = DATE_FORMATS.get(fmt_name)
    text = raw.split(" ")[0] if " " in raw and fmt_name == "auto" else raw
    if fmt is None:
        from openclear.bulk_import.fields import parse_date

        try:
            return parse_date({"issue_date": text}, "issue_date")
        except RowFieldError:
            raise RowFieldError(f"Issue date {raw!r} isn't a date", "issue_date") from None
    try:
        return datetime.strptime(text, fmt).date()
    except ValueError:
        raise RowFieldError(f"Issue date {raw!r} isn't a date in the form {fmt_name}", "issue_date") from None


def convert(settings: dict, values: dict[str, str]) -> dict[str, Any]:
    """What the issued-item import expects: account_number, check_number, amount, payee_name,
    issue_date (ISO), and void (True or False)."""
    out: dict[str, Any] = {}
    for field in ("account_number", "check_number"):
        value = (values.get(field) or "").strip()
        if field in settings.get("strip_zeros", []):
            value = value.lstrip("0") or ("0" if value else "")
        if not value:
            raise RowFieldError(f"{FIELDS[field]} is missing", field)
        out[field] = value
    void_marks = {v.strip().upper() for v in settings.get("void_values", "").split(",") if v.strip()}
    out["void"] = bool(void_marks) and (values.get("void") or "").strip().upper() in void_marks
    out["amount"] = str(_amount(settings, values))
    out["issue_date"] = _date(settings, values.get("issue_date", "")).isoformat()
    payee = (values.get("payee_name") or "").strip()
    if not payee and not out["void"]:
        raise RowFieldError("Payee is missing", "payee_name")
    out["payee_name"] = payee or "(void)"
    return out


@dataclass
class PreviewRow:
    label: str
    raw: dict[str, str]
    parsed: dict[str, Any] | None
    error: str | None
    error_field: str | None


def preview(settings: dict, filename: str, content: bytes, limit: int = MAX_SAMPLE_ROWS) -> list[PreviewRow]:
    rows = []
    for label, raw in extract(settings, filename, content)[:limit]:
        try:
            rows.append(PreviewRow(label, raw, convert(settings, raw), None, None))
        except RowFieldError as exc:
            rows.append(PreviewRow(label, raw, None, str(exc), exc.field))
    return rows


def guess(filename: str, content: bytes) -> dict:
    """First guesses for a sample file: its kind, delimiter and whether it has a header."""
    lower = filename.lower()
    if lower.endswith((".xlsx", ".xls")):
        return {"file_type": "excel", "has_header": True, "amount_format": "decimal", "date_format": "auto"}
    lines = _lines(content)[:5]
    first = lines[0] if lines else ""
    counts = {d: first.count(d) for d in DELIMITERS}
    delimiter, count = max(counts.items(), key=lambda kv: kv[1])
    if count == 0:
        return {"file_type": "fixed", "amount_format": "implied", "date_format": "MMDDYY", "positions": {}}
    has_header = bool(first) and not any(ch.isdigit() for ch in first.split(delimiter)[0])
    return {"file_type": "delimited", "delimiter": delimiter, "has_header": has_header, "amount_format": "decimal",
            "date_format": "auto", "columns": {}}
