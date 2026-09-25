# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import io
import re
from typing import Any

import pandas as pd

# Deliberately NOT csv.Sniffer / pandas' sep=None auto-detection — on a short sample
# (e.g. a header line like "account_number,check_number") the generic sniffer can pick
# the single most-frequent character in the text itself (observed: it chose 'u', since
# "account_number" contains several) rather than an actual delimiter. Constraining to a
# known set of real-world delimiters and picking whichever appears most in the header
# line is far more reliable for the files this app actually receives.
_CANDIDATE_DELIMITERS = [",", "\t", ";", "|"]
_HEADER_WHITESPACE = re.compile(r"\s+")


class TabularParseError(Exception):
    pass


def _detect_delimiter(content: bytes) -> str:
    first_line = content.split(b"\n", 1)[0].decode("utf-8", errors="replace")
    counts = {delim: first_line.count(delim) for delim in _CANDIDATE_DELIMITERS}
    best_delim, best_count = max(counts.items(), key=lambda kv: kv[1])
    return best_delim if best_count > 0 else ","


def _normalize_header(raw: str) -> str:
    return _HEADER_WHITESPACE.sub("_", str(raw).strip().lower())


def parse_tabular_file(filename: str, content: bytes) -> list[dict[str, Any]]:
    """Parses any delimited file (comma/tab/semicolon/pipe — auto-detected) or Excel
    (.xlsx/.xls) file into a list of row dicts keyed by normalized column header
    (lowercased, stripped, internal whitespace collapsed to a single underscore), so
    'Check Number', 'check_number', and 'Check   Number' all resolve to the same key and
    column order in the file doesn't matter. Everything is read as text (dtype=str)
    rather than letting pandas infer types — inferred types silently mangle exactly the
    fields this app cares most about (a check number like '007' becoming the integer 7,
    an amount losing trailing zeros) — real parsing/validation happens downstream via
    bulk_import.fields."""
    lower_name = filename.lower()
    try:
        if lower_name.endswith((".xlsx", ".xls")):
            df = pd.read_excel(io.BytesIO(content), dtype=str)
        else:
            delimiter = _detect_delimiter(content)
            df = pd.read_csv(io.BytesIO(content), sep=delimiter, dtype=str)
    except Exception as exc:  # noqa: BLE001 — pandas raises a wide variety of exception types for malformed input
        raise TabularParseError(f"Could not parse {filename!r}: {exc}") from exc

    if df.empty:
        return []

    df = df.rename(columns=_normalize_header)
    df = df.where(pd.notna(df), None)
    return df.to_dict(orient="records")
