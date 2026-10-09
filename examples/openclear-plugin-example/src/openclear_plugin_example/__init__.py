# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""An example OpenClear plugin (docs/PLUGINS.md): a fixed-width paid-items import format and
a fixed-width decision file. Import only from openclear.plugins.api, which stays stable
within a plugin API version."""

from decimal import Decimal, InvalidOperation

from openclear.plugins.api import (
    BulkFileRejected,
    BulkUploadKind,
    DecisionLayout,
    ImportSpec,
    LoadedFile,
    SourceRow,
    builtin_import_format,
    read_stored_file,
)

# Presented checks, one per line:
#   columns  1-17  account number (left-aligned, space-padded)
#   columns 18-27  check number
#   columns 28-39  amount in cents, zero-padded
#   columns 40-47  presented date, YYYYMMDD
PRESENTED_FIELDS = (("account_number", 0, 17), ("check_number", 17, 27), ("amount_cents", 27, 39), ("presented_date", 39, 47))
PRESENTED_WIDTH = 47


def _parse_presented(ref: str, filename: str) -> LoadedFile:
    try:
        text = read_stored_file(ref).decode("ascii")
    except UnicodeDecodeError:
        raise BulkFileRejected("A fixed-width file must be plain ASCII text.") from None
    rows = []
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        if len(line.rstrip("\r")) != PRESENTED_WIDTH:
            raise BulkFileRejected(f"Line {number} is {len(line)} characters; every line must be {PRESENTED_WIDTH}.")
        raw = {name: line[start:end].strip() for name, start, end in PRESENTED_FIELDS}
        # The built-in paid-items import takes dollars and an ISO date; a bad value fails
        # that row, not the file.
        try:
            amount = str(Decimal(raw.pop("amount_cents")) / 100)
        except InvalidOperation:
            amount = "not a number"
        date = raw.pop("presented_date")
        rows.append(SourceRow(f"line {number}", {**raw, "presented_amount": amount, "presented_date": f"{date[:4]}-{date[4:6]}-{date[6:]}"}))
    if not rows:
        raise BulkFileRejected("That file has no lines.")
    return LoadedFile(rows)


def _decision_file(feed: list[dict]) -> bytes:
    """One line per decision: sequence (10), account (17), check or trace number (15),
    amount in cents (12), P or R (1), return reason (3)."""
    lines = []
    for d in feed:
        cents = int(Decimal(d.get("amount") or "0") * 100)
        item = d.get("check_number") or d.get("trace_number") or ""
        lines.append(
            f"{d['sequence']:010d}{(d.get('account_number') or ''):<17.17}{item:<15.15}{cents:012d}"
            f"{'P' if d['outcome'] == 'pay' else 'R'}{(d.get('return_transaction_code') or d.get('reason_code') or ''):<3.3}"
        )
    return ("\n".join(lines) + "\n").encode("ascii", "replace") if lines else b""


class _Plugin:
    name = "example"
    version = "0.1.0"
    plugin_api = (1, 1)

    def register(self, registry) -> None:
        paid_items = builtin_import_format(BulkUploadKind.PAID_ITEMS, "tabular")
        registry.add_import_format(
            BulkUploadKind.PAID_ITEMS, "example_fixed_width",
            ImportSpec(
                _parse_presented, paid_items.ingest, resource_type=paid_items.resource_type,
                audit_action=paid_items.audit_action, audit_noun=paid_items.audit_noun,
            ),
        )
        registry.add_decision_layout(
            "example_fixed_width", DecisionLayout("Example fixed-width", "text/plain", "txt", _decision_file),
        )


plugin = _Plugin()
