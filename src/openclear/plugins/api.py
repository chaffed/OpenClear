# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""What a plugin imports from OpenClear (docs/PLUGINS.md). These names are kept stable
within a PLUGIN_API_VERSION; anything else in OpenClear may change in any release.

    from openclear.plugins.api import DecisionLayout, ImportSpec, LoadedFile, SourceRow
"""

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from openclear.domain.bulk_upload_file import BulkUploadKind
from openclear.plugins import PLUGIN_API_VERSION, PluginError, Registry
from openclear.services.bulk_import_service import (
    BulkFileRejected,
    ImportSpec,
    LoadedFile,
    RowContext,
    SourceRow,
)
from openclear.services.bulk_import_service import _read as _read_stored_file


def read_stored_file(ref: str) -> bytes:
    """The bytes of an uploaded file, given the reference an ImportSpec's `load` receives."""
    return _read_stored_file(ref)


def builtin_import_format(kind: BulkUploadKind, file_format: str) -> ImportSpec:
    """One of OpenClear's own import formats, so a plugin that only reads a different file
    layout can reuse its row handling: parse the file into the rows the built-in
    expects, and pass them to its `ingest`."""
    from openclear.services.bulk_import_service import BUILTIN_SPECS

    return BUILTIN_SPECS[(kind, file_format)]


@dataclass(frozen=True)
class DecisionLayout:
    """A decision file a core takes back. `write` receives decisions as the decision feed
    returns them (GET /api/v1/decisions; services/decision_feed_service.read_feed), oldest
    first, and returns the file's contents."""

    label: str  # shown to people, e.g. "Fineract repayment reversal CSV"
    media_type: str  # e.g. "text/csv"
    extension: str  # e.g. "csv", without the dot
    write: Callable[[list[dict]], bytes]


@dataclass(frozen=True)
class MatchingRule:
    """An extra check on every item of one network, run after that network's own rules
    (PLUGIN_PLAN.md P-D5). `check(session, transaction)` returns the exception codes to add
    (from `exception_types`), or nothing; it can't clear what the built-in rules found.
    The transaction is a PaidItem for "check" and an AchTransaction for "ach".

    If `check` raises, the item gets the `plugin_rule_failed` exception instead, so a
    broken rule sends items to a person rather than letting them through."""

    name: str
    network: str  # "check", "ach", or a network a plugin adds
    exception_types: dict[str, str]  # code -> what people see, e.g. {"payee_on_watchlist": "Payee on watch list"}
    check: Callable[[Any, Any], Iterable[str]]


@dataclass(frozen=True)
class ScoringFeature:
    """Extra inputs to the fraud model for one network's exceptions. `build(session,
    exception_item)` returns numbers (or strings, which are one-hot encoded) keyed by name;
    they reach the model as `plugin:<name>:<key>`. Features apply to every bank, because
    the shared model is trained on every participating bank's decisions."""

    name: str
    network: str
    build: Callable[[Any, Any], dict[str, Any]]


@dataclass(frozen=True)
class ConfigField:
    """A setting a bank fills in when it turns on a notification channel."""

    name: str
    label: str
    secret: bool = False  # stored encrypted and never shown again
    required: bool = True


@dataclass(frozen=True)
class NotificationChannel:
    """Somewhere a bank's events are sent, such as a chat tool. `send(event, config)` gets
    the event (`type`, `timestamp`, `data`, `bank`, `link`) and the bank's settings. It runs
    as a background job; if it raises, it's retried."""

    name: str
    label: str
    send: Callable[[dict[str, Any], dict[str, str]], None]
    config_fields: tuple[ConfigField, ...] = ()
    # The event types it receives (services/webhook_service.EVENT_TYPES); None for all.
    events: tuple[str, ...] | None = None
    description: str = field(default="")


def source_item(session, exception_item):
    """The item an exception is about: a PaidItem for a check, an AchTransaction for ACH."""
    from openclear.networks.registry import get_adapter

    return get_adapter(exception_item.network_code).load_source_item(session, exception_item.source_item_id)


def apply_matching_rules(session, network_code: str, transaction, result):
    """For a network plugin's adapter: call at the end of `evaluate` with the network's own
    MatchResult, so banks' matching-rule plugins apply to it too (the built-in networks do)."""
    from openclear.services import plugin_service

    return plugin_service.apply_matching_rules(session, network_code, transaction, result)


def extra_features(session, network_code: str, exception_item) -> dict[str, Any]:
    """For a network plugin's adapter: merge into what `build_features` returns."""
    from openclear.services import plugin_service

    return plugin_service.extra_features(session, network_code, exception_item)


__all__ = [
    "PLUGIN_API_VERSION", "BulkFileRejected", "BulkUploadKind", "ConfigField", "DecisionLayout", "ImportSpec", "LoadedFile",
    "MatchingRule", "NotificationChannel", "PluginError", "Registry", "RowContext", "ScoringFeature", "SourceRow",
    "apply_matching_rules", "builtin_import_format", "extra_features", "read_stored_file", "source_item",
]
