# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Reading a file by a layout (UX_PLAN.md U1; domain/import_layout.py): a customer's
issued-check file, or the posted-check file a bank's core sends (PAID_ITEM_LAYOUTS_PLAN.md).

A layout's settings say what kind of items it reads (`kind`: "issued_items", the default for
layouts saved before there was a choice, or "paid_items"), how the file is laid out and where
each field is:

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

A posted-check layout (PAID_ITEM_LAYOUTS_PLAN.md PIL-D3, PIL-D4) also has:

- `onus_mode`, `onus_separator`, `onus_order`, `onus_account` and `onus_serial`: how the
  MICR on-us field splits into the account and the check number, when the file gives only that.
- `debit_values`: the transaction codes that mean a check debit; other lines are skipped.
- `routing_numbers`: the bank's routing numbers; a line for another one fails.
- `trailer`: the line carrying the file's item count and total, checked before anything is
  imported (`check_trailer`).

Reading a file gives each item's raw text per field (`extract`); `convert` turns one into
what the import expects, raising RowFieldError naming the field at fault, or RowSkipped for a
posted-check line that isn't a check to present. Values are always trimmed."""

import csv
import io
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Callable

from openclear.bulk_import.fields import RowFieldError

ISSUED_ITEMS, PAID_ITEMS = "issued_items", "paid_items"

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
PAID_FIELDS: dict[str, str] = {
    "account_number": "Account number",
    "check_number": "Check number",
    "micr_onus": "MICR on-us field",
    "amount": "Amount",
    "amount_cents": "Cents (separate column)",
    "posted_date": "Posted date",
    "routing_number": "Routing number",
    "transaction_code": "Transaction code",
    "sequence": "Sequence or item number",
}
# Account and check number, or the on-us field they're both in (clean_settings).
PAID_REQUIRED = ("amount", "posted_date")
ONUS_MODES = {"separator": "Split at a character", "positions": "Characters at fixed places"}
ONUS_ORDERS = {"account_serial": "Account, then check number", "serial_account": "Check number, then account"}
TRAILER_MATCHES = {"": "No trailer", "prefix": "The line starting with…", "last": "The last line"}
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


class RowSkipped(Exception):
    """A posted-check line that isn't a check to present (another transaction code, or a
    reversal): left out and counted, not failed."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


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

# The same, for a core's posted-check file (PAID_ITEM_LAYOUTS_PLAN.md PIL-D6).
PAID_TEMPLATES: dict[str, Template] = {
    "fixed_header_trailer": Template(
        "Fixed-width with header and trailer", "H header, D detail lines (account 12, space-padded; check 10, amount in cents 12, posted "
        "date MMDDYYYY 8, transaction code 3), and a T trailer with the item count (7) and total in cents (15).",
        {"kind": PAID_ITEMS, "file_type": "fixed", "line_prefix": "D",
         "positions": {"account_number": {"start": 2, "length": 12}, "check_number": {"start": 14, "length": 10},
                       "amount": {"start": 24, "length": 12}, "posted_date": {"start": 36, "length": 8},
                       "transaction_code": {"start": 44, "length": 3}},
         "amount_format": "implied", "date_format": "MMDDYYYY", "strip_zeros": ["check_number"], "debit_values": "200",
         "trailer": {"match": "prefix", "prefix": "T", "count_start": 2, "count_length": 7, "total_start": 9, "total_length": 15}}),
    "posted_csv": Template(
        "CSV with column names", "A header row naming each column; amounts like 1,234.56.",
        {"kind": PAID_ITEMS, "file_type": "delimited", "delimiter": ",", "has_header": True,
         "columns": {"account_number": {"name": "Account"}, "check_number": {"name": "Check Number"}, "amount": {"name": "Amount"},
                     "posted_date": {"name": "Posted Date"}},
         "amount_format": "decimal", "date_format": "auto"}),
    "micr_onus": Template(
        "MICR on-us line", "Routing number, the on-us field (account/check number, split at the /), amount and posted date, "
        "comma-separated with no header.",
        {"kind": PAID_ITEMS, "file_type": "delimited", "delimiter": ",", "has_header": False,
         "columns": {"routing_number": {"position": 1}, "micr_onus": {"position": 2}, "amount": {"position": 3},
                     "posted_date": {"position": 4}},
         "amount_format": "decimal", "date_format": "auto", "onus_mode": "separator", "onus_separator": "/",
         "onus_order": "account_serial", "strip_zeros": ["check_number"]}),
}


def kind_of(settings: dict) -> str:
    return settings.get("kind") or ISSUED_ITEMS


def fields_for(kind: str) -> dict[str, str]:
    return PAID_FIELDS if kind == PAID_ITEMS else FIELDS


def templates_for(kind: str) -> dict[str, Template]:
    return PAID_TEMPLATES if kind == PAID_ITEMS else TEMPLATES


# --- Settings ----------------------------------------------------------------------------------


def _int(value: Any, what: str, minimum: int = 0) -> int:
    try:
        number = int(str(value).strip() or 0)
    except ValueError:
        raise LayoutError(f"{what} must be a whole number.") from None
    if number < minimum:
        raise LayoutError(f"{what} must be at least {minimum}.")
    return number


def _spot(raw: dict, start_key: str, length_key: str, what: str) -> dict | None:
    if not str(raw.get(start_key) or "").strip():
        return None
    return {"start": _int(raw[start_key], f"{what}'s start", 1), "length": _int(raw.get(length_key, 0), f"{what}'s length", 1)}


def _clean_paid(raw: dict, settings: dict, mapped: dict, complete: bool) -> None:
    """A posted-check layout's own settings (PAID_ITEM_LAYOUTS_PLAN.md PIL-D3, PIL-D4)."""
    onus_mode = raw.get("onus_mode") or "separator"
    if onus_mode not in ONUS_MODES:
        raise LayoutError("Choose how the on-us field splits.")
    if "micr_onus" in mapped:
        settings["onus_mode"] = onus_mode
        if onus_mode == "separator":
            separator = str(raw.get("onus_separator") or "")
            if len(separator) != 1 and complete:
                raise LayoutError("The on-us field's separator is one character, such as /.")
            order = raw.get("onus_order") or "account_serial"
            if order not in ONUS_ORDERS:
                raise LayoutError("Choose which comes first in the on-us field.")
            settings["onus_separator"], settings["onus_order"] = separator, order
        else:
            for part, what in (("account", "The account in the on-us field"), ("serial", "The check number in the on-us field")):
                spot = _spot(raw.get(f"onus_{part}") or {}, "start", "length", what)
                if spot is None and complete:
                    raise LayoutError(f"Say where {what[0].lower() + what[1:]} is.")
                if spot is not None:
                    settings[f"onus_{part}"] = spot
    settings["debit_values"] = ", ".join(v.strip() for v in str(raw.get("debit_values") or "").split(",") if v.strip())
    if "transaction_code" in mapped and not settings["debit_values"] and complete:
        raise LayoutError("Say which transaction codes are check debits, such as 200.")
    routing = [r.strip() for r in str(raw.get("routing_numbers") or "").split(",") if r.strip()]
    if any(not (r.isdigit() and len(r) == 9) for r in routing):
        raise LayoutError("Routing numbers are 9 digits, comma-separated.")
    settings["routing_numbers"] = ", ".join(routing)

    trailer = raw.get("trailer") or {}
    match = trailer.get("match") or ""
    if match not in TRAILER_MATCHES:
        raise LayoutError("Choose how to find the trailer.")
    if not match:
        return
    cleaned: dict[str, Any] = {"match": match}
    if match == "prefix":
        cleaned["prefix"] = str(trailer.get("prefix") or "")
        if not cleaned["prefix"]:
            raise LayoutError("Say what the trailer line starts with, such as T.")
    if settings["file_type"] == "fixed":
        for part, what in (("count", "The trailer's item count"), ("total", "The trailer's total")):
            spot = _spot(trailer, f"{part}_start", f"{part}_length", what)
            if spot is None and complete:
                raise LayoutError(f"Say where {what[0].lower() + what[1:]} is.")
            if spot is not None:
                cleaned[f"{part}_start"], cleaned[f"{part}_length"] = spot["start"], spot["length"]
    else:
        for part, what in (("count", "The trailer's item count"), ("total", "The trailer's total")):
            column = str(trailer.get(f"{part}_column") or "").strip()
            if not column and complete:
                raise LayoutError(f"Say which column of the trailer holds {what[4:].lower()}.")
            if column:
                cleaned[f"{part}_column"] = _int(column, f"{what}'s column", 1)
    settings["trailer"] = cleaned


def clean_settings(raw: dict, *, kind: str | None = None, complete: bool = True) -> dict:
    """The settings, checked and in their stored form. Raises LayoutError. With complete
    False (the builder, before every field is placed), missing fields are allowed. `kind`
    defaults to the one the settings name."""
    kind = kind or kind_of(raw)
    if kind not in (ISSUED_ITEMS, PAID_ITEMS):
        raise LayoutError("Choose what the file holds.")
    fields = fields_for(kind)
    file_type = raw.get("file_type")
    if file_type not in FILE_TYPES:
        raise LayoutError("Choose the kind of file.")
    settings: dict[str, Any] = {"kind": kind, "file_type": file_type}
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
            if field not in fields or not spot or not str(spot.get("start", "")).strip():
                continue
            positions[field] = {"start": _int(spot["start"], f"{fields[field]}'s start", 1),
                                "length": _int(spot.get("length", 0), f"{fields[field]}'s length", 1)}
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
            if field not in fields or not ref:
                continue
            if ref.get("name"):
                if not settings["has_header"]:
                    raise LayoutError(f"{fields[field]} names a column, but the file has no header row; use its position.")
                columns[field] = {"name": str(ref["name"]).strip()}
            elif str(ref.get("position", "")).strip():
                columns[field] = {"position": _int(ref["position"], f"{fields[field]}'s column", 1)}
        settings["columns"] = columns
        mapped = columns
    if kind == PAID_ITEMS:
        settings.pop("void_values")
        _clean_paid(raw, settings, mapped, complete)
        required = list(PAID_REQUIRED) + ([] if "micr_onus" in mapped else ["account_number", "check_number"])
        if "micr_onus" in mapped and ("account_number" in mapped or "check_number" in mapped):
            raise LayoutError("Use either the on-us field, or the account and check number, not both.")
    else:
        required = list(REQUIRED)
    missing = [fields[f] for f in required if f not in mapped]
    if missing and complete:
        raise LayoutError("Say where to find: " + ", ".join(missing) + ".")
    if "void" in mapped and not settings.get("void_values") and complete:
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


def _split_trailer(settings: dict, records: list, first: Callable[[Any], str], *, after: int = 0) -> tuple[list, Any]:
    """(the records without the trailer, the trailer or None). The trailer is found among all
    the file's lines, before any are skipped, and is never read as an item."""
    trailer = settings.get("trailer") or {}
    match = trailer.get("match")
    if not match or len(records) <= after:
        return records, None
    if match == "last":
        return records[:-1], records[-1]
    for index in range(len(records) - 1, after - 1, -1):
        if first(records[index]).startswith(trailer["prefix"]):
            return records[:index] + records[index + 1:], records[index]
    return records, None


def _records(settings: dict, content: bytes) -> tuple[list[str] | None, list, Any]:
    """(header, records, trailer) before skipping: lines for fixed-width, rows of cells otherwise."""
    file_type = settings["file_type"]
    if file_type == "fixed":
        lines, trailer = _split_trailer(settings, _lines(content), lambda line: line)
        return None, lines, trailer
    if file_type == "excel":
        rows = _excel_rows(content, settings.get("sheet", ""))
    else:
        rows = [row for row in csv.reader(io.StringIO(_text(content)), delimiter=settings.get("delimiter", ",")) if any(c.strip() for c in row)]
    has_header = bool(settings.get("has_header") and rows)
    rows, trailer = _split_trailer(settings, rows, lambda row: (row[0] if row else "").strip(), after=1 if has_header else 0)
    header = None
    if has_header:
        header, rows = [c.strip() for c in rows[0]], rows[1:]
    return header, rows, trailer


def grid(settings: dict, filename: str, content: bytes) -> tuple[list[str] | None, list[list[str]] | list[str]]:
    """The file as the layout sees it: (header, rows of cells) for delimited and Excel, or
    (None, item lines) for fixed-width. Skipped lines and the trailer are already left out."""
    header, records, _trailer = _records(settings, content)
    if settings["file_type"] == "fixed":
        lines = _trim(records, settings)
        prefix = settings.get("line_prefix") or ""
        return None, [line for line in lines if line.startswith(prefix)]
    return header, _trim(records, settings)


def _column_index(header: list[str] | None, ref: dict, field: str) -> int:
    if "position" in ref:
        return ref["position"] - 1
    wanted = ref["name"].strip().lower()
    for index, name in enumerate(header or []):
        if name.strip().lower() == wanted:
            return index
    raise LayoutError(f"The file has no column named {ref['name']!r} ({FIELDS.get(field) or PAID_FIELDS[field]}).")


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


def _date(settings: dict, raw: str, field: str = "issue_date") -> date:
    what = FIELDS.get(field) or PAID_FIELDS[field]
    if not raw:
        raise RowFieldError(f"{what} is missing", field)
    fmt_name = settings.get("date_format", "auto")
    fmt = DATE_FORMATS.get(fmt_name)
    text = raw.split(" ")[0] if " " in raw and fmt_name == "auto" else raw
    if fmt is None:
        from openclear.bulk_import.fields import parse_date

        try:
            return parse_date({field: text}, field)
        except RowFieldError:
            raise RowFieldError(f"{what} {raw!r} isn't a date", field) from None
    try:
        return datetime.strptime(text, fmt).date()
    except ValueError:
        raise RowFieldError(f"{what} {raw!r} isn't a date in the form {fmt_name}", field) from None


def _is_reversal(raw: str) -> bool:
    """A posted amount written negative: a leading or trailing minus, or in parentheses."""
    text = raw.replace(" ", "")
    return text.startswith("-") or text.endswith("-") or (text.startswith("(") and text.endswith(")"))


def _onus(settings: dict, raw: str) -> tuple[str, str]:
    """(account, check number) from the MICR on-us field."""
    if not raw:
        raise RowFieldError("MICR on-us field is missing", "micr_onus")
    if settings.get("onus_mode") == "positions":
        parts = {part: raw[spot["start"] - 1: spot["start"] - 1 + spot["length"]].strip()
                 for part in ("account", "serial") for spot in [settings[f"onus_{part}"]]}
        return parts["account"], parts["serial"]
    separator = settings.get("onus_separator") or "/"
    pieces = [p.strip() for p in raw.split(separator) if p.strip()]
    if len(pieces) != 2:
        raise RowFieldError(f"MICR on-us field {raw!r} doesn't split into an account and a check number at {separator!r}", "micr_onus")
    first, second = pieces
    return (first, second) if settings.get("onus_order", "account_serial") == "account_serial" else (second, first)


def _convert_paid(settings: dict, values: dict[str, str]) -> dict[str, Any]:
    """What the paid-item import expects (networks/check/bulk_import.py::ingest_paid_item_tabular_row):
    account_number, check_number, presented_amount and presented_date (ISO), with
    routing_number and sequence when the file has them. Raises RowSkipped for a line that
    isn't a check debit, or a reversal."""
    debit_values = {v.strip().upper() for v in settings.get("debit_values", "").split(",") if v.strip()}
    code = (values.get("transaction_code") or "").strip()
    if debit_values and code.upper() not in debit_values:
        raise RowSkipped(f"transaction code {code!r} isn't a check debit")
    if _is_reversal(values.get("amount", "")):
        raise RowSkipped("a reversal")
    out: dict[str, Any] = {}
    if "micr_onus" in values:
        out["account_number"], out["check_number"] = _onus(settings, (values.get("micr_onus") or "").strip())
        blank = "account_number" if not out["account_number"] else "check_number" if not out["check_number"] else None
        if blank:
            raise RowFieldError(f"MICR on-us field has no {PAID_FIELDS[blank].lower()}", "micr_onus")
    else:
        for field in ("account_number", "check_number"):
            out[field] = (values.get(field) or "").strip()
            if not out[field]:
                raise RowFieldError(f"{PAID_FIELDS[field]} is missing", field)
    for field in ("account_number", "check_number"):
        if field in settings.get("strip_zeros", []):
            out[field] = out[field].lstrip("0") or "0"
    routing = (values.get("routing_number") or "").strip()
    allowed = {r.strip() for r in settings.get("routing_numbers", "").split(",") if r.strip()}
    if routing and allowed and routing not in allowed:
        raise RowFieldError(f"Routing number {routing} isn't one of the bank's", "routing_number")
    out["presented_amount"] = str(_amount(settings, values))
    out["presented_date"] = _date(settings, values.get("posted_date", ""), "posted_date").isoformat()
    if routing:
        out["routing_number"] = routing
    if values.get("sequence"):
        out["sequence"] = values["sequence"].strip()
    return out


def convert(settings: dict, values: dict[str, str]) -> dict[str, Any]:
    """What the import expects. For an issued-check layout: account_number, check_number,
    amount, payee_name, issue_date (ISO), and void (True or False); for a posted-check one,
    see _convert_paid."""
    if kind_of(settings) == PAID_ITEMS:
        return _convert_paid(settings, values)
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
    skipped: str | None = None


def preview(settings: dict, filename: str, content: bytes, limit: int = MAX_SAMPLE_ROWS) -> list[PreviewRow]:
    rows = []
    for label, raw in extract(settings, filename, content)[:limit]:
        try:
            rows.append(PreviewRow(label, raw, convert(settings, raw), None, None))
        except RowSkipped as exc:
            rows.append(PreviewRow(label, raw, None, None, None, skipped=exc.reason))
        except RowFieldError as exc:
            rows.append(PreviewRow(label, raw, None, str(exc), exc.field))
    return rows


@dataclass(frozen=True)
class TrailerCheck:
    """A posted-check file's trailer against its detail lines (PAID_ITEM_LAYOUTS_PLAN.md PIL-D4).
    The trailer counts every detail line the layout reads (skipped ones too) and totals their
    amounts, a reversal counting against it."""

    declared_count: int | None
    declared_total: Decimal | None
    found_count: int
    found_total: Decimal
    problem: str | None  # None when they agree

    @property
    def ok(self) -> bool:
        return self.problem is None


def _signed_amount(settings: dict, raw: str) -> Decimal | None:
    try:
        amount = _amount(settings, {"amount": raw.replace("-", "").strip("()")})
    except RowFieldError:
        return None
    return -amount if _is_reversal(raw) else amount


def check_trailer(settings: dict, filename: str, content: bytes) -> TrailerCheck | None:
    """None when the layout has no trailer. Otherwise the declared and found count and total,
    and what's wrong when they differ or the trailer is missing or unreadable."""
    trailer_settings = settings.get("trailer") or {}
    if not trailer_settings.get("match"):
        return None
    _header, _records_, trailer = _records(settings, content)
    items = extract(settings, filename, content)
    amounts = [_signed_amount(settings, values.get("amount", "")) for _label, values in items]
    found_count, found_total = len(items), sum((a for a in amounts if a is not None), Decimal("0"))

    def result(problem, count=None, total=None):
        return TrailerCheck(count, total, found_count, found_total, problem)

    if trailer is None:
        return result("The file has no trailer line, so its count and total can't be checked.")
    if settings["file_type"] == "fixed":
        raw_count = trailer[trailer_settings["count_start"] - 1: trailer_settings["count_start"] - 1 + trailer_settings["count_length"]]
        raw_total = trailer[trailer_settings["total_start"] - 1: trailer_settings["total_start"] - 1 + trailer_settings["total_length"]]
    else:
        cells = [c.strip() for c in trailer]
        raw_count = cells[trailer_settings["count_column"] - 1] if trailer_settings["count_column"] <= len(cells) else ""
        raw_total = cells[trailer_settings["total_column"] - 1] if trailer_settings["total_column"] <= len(cells) else ""
    raw_count, raw_total = raw_count.strip(), raw_total.strip()
    if not raw_count.isdigit():
        return result(f"The trailer's item count {raw_count!r} isn't a number.")
    total = _signed_amount(settings, raw_total) if raw_total.strip("0") else Decimal("0.00")
    if total is None:
        return result(f"The trailer's total {raw_total!r} isn't an amount.", int(raw_count))
    count = int(raw_count)
    unreadable = sum(a is None for a in amounts)
    if count != found_count or total != found_total or unreadable:
        detail = f" ({unreadable} line{'s' if unreadable != 1 else ''} with an amount that can't be read)" if unreadable else ""
        return result(f"The trailer says {count} items totalling {total:,.2f}; the file has {found_count} totalling "
                      f"{found_total:,.2f}{detail}.", count, total)
    return result(None, count, total)


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
