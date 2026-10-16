# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""What a plugin imports from OpenClear (docs/PLUGINS.md). These names are kept stable
within a PLUGIN_API_VERSION; anything else in OpenClear may change in any release. UI slots
and a plugin's own pages joined version 1 in 1.17 (MOBILE_PLAN.md R5).

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
    """A decision file a core takes back. `write(feed, settings)` receives decisions as the
    decision feed returns them (GET /api/v1/decisions; services/decision_feed_service.read_feed),
    oldest first, and the bank's settings for this layout (its `config_fields`, filled in
    under Settings → Decision files), and returns the file's contents."""

    label: str  # shown to people, e.g. "Fineract repayment reversal CSV"
    media_type: str  # e.g. "text/csv"
    extension: str  # e.g. "csv", without the dot
    write: Callable[[list[dict], dict[str, str]], bytes]
    config_fields: tuple = ()  # ConfigField, below


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


# --- UI slots (plugins/ui_slots.py) and a plugin's own pages ----------------------------------

from openclear.plugins.ui_slots import SlotAction, SlotContent, SlotContext, UiSlot  # noqa: E402


# FastAPI dependencies for a plugin's own routes: `Depends(web_context)` is the signed-in
# person's TenantContext (or a redirect to sign in), `Depends(require_web_permission("..."))`
# also needs a permission, `Depends(verify_csrf)` refuses a form post without the page's CSRF
# token (a slot's "post" action sends it), and `Depends(database)` is the request's session,
# bound to the person's bank.
from openclear.db.session import get_db as database  # noqa: E402
from openclear.web.deps import get_web_context as web_context  # noqa: E402
from openclear.web.deps import require_web_permission  # noqa: E402
from openclear.web.security import verify_csrf  # noqa: E402


def render_page(request, ctx, *, title: str, html: str, status_code: int = 200):
    """A page in OpenClear's layout (navigation, branding, help) around the plugin's HTML, which
    is sanitised as a slot's is."""
    from openclear.plugins.ui_slots import sanitise
    from openclear.web.deps import render_template

    return render_template(request, "plugins/page.html", ctx=ctx, page_title=title, page_html=sanitise(html), status_code=status_code)


def record_action(session, ctx, *, action: str, summary: str, resource_id=None) -> None:
    """Record something the plugin did in the bank's action log, against the person, before the
    plugin commits. `action` should start with the plugin's name ("example.score_refreshed")."""
    from openclear.services import audit_log_service

    audit_log_service.record_action(session, ctx.tenant_id, actor_user_id=ctx.user_id, channel="web", action=action, summary=summary,
                                     resource_type="plugin", resource_id=resource_id)


def get_exception(session, ctx, exception_id):
    """An exception the person may see (their bank, their customer and accounts), or None."""
    import uuid as _uuid

    from openclear.repositories.exception_repo import ExceptionRepository

    try:
        wanted = exception_id if isinstance(exception_id, _uuid.UUID) else _uuid.UUID(str(exception_id))
    except ValueError:
        return None
    return ExceptionRepository(session, ctx.tenant_id, ctx.customer_id).get(wanted)


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
    "MatchingRule", "NotificationChannel", "PluginError", "Registry", "RowContext", "ScoringFeature", "SlotAction", "SlotContent",
    "SlotContext", "SourceRow", "UiSlot", "apply_matching_rules", "builtin_import_format", "database", "extra_features",
    "get_exception", "read_stored_file", "record_action", "render_page", "require_web_permission", "source_item", "verify_csrf", "web_context",
]
