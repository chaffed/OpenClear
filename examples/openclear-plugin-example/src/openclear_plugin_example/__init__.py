# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""An example OpenClear plugin (docs/PLUGINS.md), with one of each kind of extension a bank
chooses:

- a fixed-width paid-items import format and a fixed-width decision file;
- a matching rule: a large check for a round amount is an exception;
- a scoring feature: whether a check's amount is a round number;
- a notification channel that records what it's sent (a real one would post to a chat tool).

Import only from openclear.plugins.api, which stays stable within a plugin API version."""

import logging
from decimal import Decimal, InvalidOperation

from openclear.plugins.api import (
    BulkFileRejected,
    BulkUploadKind,
    ConfigField,
    DecisionLayout,
    ImportSpec,
    LoadedFile,
    MatchingRule,
    NotificationChannel,
    ScoringFeature,
    SourceRow,
    builtin_import_format,
    read_stored_file,
    source_item,
)

logger = logging.getLogger(__name__)

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


def _decision_file(feed: list[dict], settings: dict) -> bytes:
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


LARGE_ROUND_AMOUNT = Decimal("10000")


def _large_round_amount(session, paid_item) -> list[str]:
    """Round-thousand checks of $10,000 or more: a pattern in altered and counterfeit checks."""
    amount = Decimal(paid_item.presented_amount)
    return ["large_round_amount"] if amount >= LARGE_ROUND_AMOUNT and amount % 1000 == 0 else []


def _round_amount_feature(session, exception_item) -> dict:
    paid_item = source_item(session, exception_item)
    amount = Decimal(paid_item.presented_amount) if paid_item else Decimal(0)
    return {"round_dollars": amount % 1 == 0, "round_thousands": amount % 1000 == 0}


# What the example channel was asked to send, for tests; a real channel would post it.
SENT: list[tuple[dict, dict]] = []


def _record(event: dict, config: dict) -> None:
    SENT.append((event, config))
    logger.info("%s %s: %s", config.get("prefix", ""), event["type"], event.get("link") or event["data"])


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
        registry.add_matching_rule(MatchingRule(
            "example_large_round_amount", "check", {"large_round_amount": "Large round-amount check"}, _large_round_amount,
        ))
        registry.add_scoring_feature(ScoringFeature("example_round_amount", "check", _round_amount_feature))
        registry.add_notification_channel(NotificationChannel(
            "example_log", "Example log channel", _record,
            config_fields=(ConfigField("prefix", "Message prefix"), ConfigField("token", "Access token", secret=True, required=False)),
            events=("exception.created", "exception.decided"),
        ))


plugin = _Plugin()
