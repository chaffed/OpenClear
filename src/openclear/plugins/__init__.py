# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Plugins: extending OpenClear without changing it (PLUGIN_PLAN.md).

A plugin is a Python package that declares an entry point in the `openclear.plugins`
group, pointing at an object with:

- `name` and `version` (strings);
- `plugin_api` — the range of PLUGIN_API_VERSION it supports, as `(lowest, highest)`;
- `register(registry)` — called once at startup, adding to the extension points below.

For example, in the plugin's pyproject.toml:

    [project.entry-points."openclear.plugins"]
    rtp = "openclear_rtp:plugin"

The operator allows it with `OPENCLEAR_PLUGINS=rtp` (a comma-separated list of entry point
names; installing a package alone activates nothing), or `OPENCLEAR_PLUGINS_REQUIRED=rtp`
to also refuse to start without it. A plugin that isn't allowed, needs
another plugin API version, fails to load, or claims a name something else already has is
reported (Admin → Plugins, and the log) and skipped; the app still starts. OpenClear's own
built-in network, OCR, notification, storage, scoring and import-format code registers
the same way (plugins/builtin.py).

Where using an entry is a bank's choice (BANK_CHOICE_POINTS: import formats, decision
layouts), a plugin's entry is off for each bank until its admin turns it on.

The stable names a plugin imports are in plugins/api.py.

Plugins run inside OpenClear with its full access, so only an operator installs them. A
plugin loaded into OpenClear is part of the same program, so it's AGPL-3.0 too
(PLUGIN_PLAN.md P-D1); closed integrations use the API and webhooks instead.
"""

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from importlib.metadata import entry_points
from typing import Any

from openclear.config import get_settings

logger = logging.getLogger(__name__)

PLUGIN_API_VERSION = 1
ENTRY_POINT_GROUP = "openclear.plugins"

EXTENSION_POINTS = (
    "networks", "ocr_providers", "email_providers", "sms_providers", "storage_backends", "scoring_models", "import_formats",
    "decision_layouts", "matching_rules", "scoring_features", "notification_channels",
)
BUILTIN_NAME = "openclear"
# Extension points where using a plugin's entry is each bank's choice: a bank admin turns it
# on under Admin → Plugins (services/plugin_service.py). The rest are the operator's choice,
# made in configuration. Built-in entries are always on.
BANK_CHOICE_POINTS = ("import_formats", "decision_layouts", "matching_rules", "notification_channels")
# Exception codes a matching rule plugin may not claim: the built-in rules' own, and the one
# a failing plugin rule raises (services/plugin_service.RULE_FAILED).
RESERVED_EXCEPTION_CODES = {"plugin_rule_failed"}


class PluginError(Exception):
    """A plugin did something the registry won't accept (a name already taken, say)."""


@dataclass
class PluginInfo:
    name: str
    version: str
    source: str  # "built-in", or the installed distribution it came from
    status: str  # loaded, not allowed, incompatible, failed
    error: str | None = None
    provides: list[str] = field(default_factory=list)


class Registry:
    """What every extension point resolves names through. Each `add_*` refuses a name that's
    already registered, so one plugin can't silently replace another's. A plugin's
    registration is all or nothing: if `register()` raises, what it added is taken back."""

    def __init__(self) -> None:
        self.networks: dict[str, Any] = {}
        self.ocr_providers: dict[str, Callable[[], Any]] = {}
        self.email_providers: dict[str, Callable[[], Any]] = {}
        self.sms_providers: dict[str, Callable[[], Any]] = {}
        self.storage_backends: dict[str, Callable[[], Any]] = {}
        self.scoring_models: dict[str, Callable[[], Any]] = {}
        self.import_formats: dict[tuple[Any, str], Any] = {}
        self.decision_layouts: dict[str, Any] = {}
        self.matching_rules: dict[str, Any] = {}
        self.scoring_features: dict[str, Any] = {}
        self.notification_channels: dict[str, Any] = {}
        self.exception_type_labels: dict[str, str] = {}  # codes plugin rules add, and their names
        self.origins: dict[tuple[str, Any], str] = {}  # (point, key) -> the plugin that registered it
        self._current: PluginInfo | None = None
        self._added: list[tuple[str, Any]] = []
        self._added_labels: list[str] = []
        self._networks_pending: list[Any] = []

    def _add(self, point: str, key, value) -> None:
        table = getattr(self, point)
        if key in table:
            raise PluginError(f"{point} already has {key!r}")
        table[key] = value
        self._added.append((point, key))
        if self._current is not None:
            self.origins[(point, key)] = self._current.name
            self._current.provides.append(f"{point}: {label(key)}")

    def add_network(self, adapter) -> None:
        """A payment network (networks.base.NetworkAdapter), registered under its `code`."""
        self._add("networks", adapter.code, adapter)
        self._networks_pending.append(adapter)  # made live once the whole plugin registers

    def add_ocr_provider(self, name: str, factory: Callable[[], Any]) -> None:
        self._add("ocr_providers", name, factory)

    def add_email_provider(self, name: str, factory: Callable[[], Any]) -> None:
        self._add("email_providers", name, factory)

    def add_sms_provider(self, name: str, factory: Callable[[], Any]) -> None:
        self._add("sms_providers", name, factory)

    def add_storage_backend(self, name: str, factory: Callable[[], Any]) -> None:
        self._add("storage_backends", name, factory)

    def add_scoring_model(self, name: str, factory: Callable[[], Any]) -> None:
        self._add("scoring_models", name, factory)

    def add_import_format(self, kind, file_format: str, spec) -> None:
        """A bulk file format (services/bulk_import_service.ImportSpec) for an upload kind."""
        self._add("import_formats", (kind, file_format), spec)

    def add_decision_layout(self, name: str, layout) -> None:
        """A decision file a core takes back (plugins/api.py DecisionLayout): given decisions
        from the feed, the file's bytes."""
        self._add("decision_layouts", name, layout)

    def add_matching_rule(self, rule) -> None:
        """An extra check run after a network's own rules (plugins/api.py MatchingRule). It can
        only add exception types, never clear one, so it can't let through an item the
        built-in rules flagged."""
        taken = _builtin_exception_codes() | RESERVED_EXCEPTION_CODES | set(self.exception_type_labels)
        clash = sorted(set(rule.exception_types) & taken)
        if clash:
            raise PluginError(f"exception types already defined: {', '.join(clash)}")
        self._add("matching_rules", rule.name, rule)
        self.exception_type_labels.update(rule.exception_types)
        self._added_labels.extend(rule.exception_types)

    def add_scoring_feature(self, feature) -> None:
        """Extra inputs to the fraud model for one network (plugins/api.py ScoringFeature)."""
        self._add("scoring_features", feature.name, feature)

    def add_notification_channel(self, channel) -> None:
        """Somewhere a bank's events can be sent, such as a chat tool (plugins/api.py
        NotificationChannel). Each bank turns it on and fills in its settings."""
        self._add("notification_channels", channel.name, channel)


registry = Registry()
_status: list[PluginInfo] = []
_loaded = False
_lock = threading.Lock()


def _builtin_exception_codes() -> set[str]:
    from openclear.networks.ach.types import AchExceptionType
    from openclear.networks.check.types import CheckExceptionType

    return {e.value for e in CheckExceptionType} | {e.value for e in AchExceptionType}


def label(key) -> str:
    """How an extension point's key is written: "paid_items/tabular" for an import format."""
    return f"{key[0].value}/{key[1]}" if isinstance(key, tuple) else str(key)


def _names(value: str) -> set[str]:
    return {name.strip() for name in (value or "").split(",") if name.strip()}


def _allowed() -> set[str]:
    return _names(get_settings().plugins) | _names(get_settings().plugins_required)


def _supports(plugin) -> bool:
    lowest, highest = getattr(plugin, "plugin_api", (0, -1))
    return lowest <= PLUGIN_API_VERSION <= highest


def _register(plugin, source: str) -> PluginInfo:
    info = PluginInfo(name=getattr(plugin, "name", "?"), version=str(getattr(plugin, "version", "?")), source=source, status="loaded")
    if not _supports(plugin):
        info.status = "incompatible"
        info.error = f"supports plugin API {getattr(plugin, 'plugin_api', '?')}, this OpenClear has {PLUGIN_API_VERSION}"
        return info
    from openclear.networks.registry import register_adapter

    registry._current, registry._added, registry._networks_pending, registry._added_labels = info, [], [], []
    try:
        plugin.register(registry)
    except Exception as exc:  # noqa: BLE001 -- a broken plugin is reported, not fatal
        info.status, info.error, info.provides = "failed", f"{type(exc).__name__}: {exc}", []
        for point, key in registry._added:
            getattr(registry, point).pop(key, None)
            registry.origins.pop((point, key), None)
        for code in registry._added_labels:
            registry.exception_type_labels.pop(code, None)
        logger.exception("Plugin %s failed to register", info.name)
    else:
        for adapter in registry._networks_pending:
            register_adapter(adapter)
    finally:
        registry._current, registry._added, registry._networks_pending, registry._added_labels = None, [], [], []
    return info


def load() -> list[PluginInfo]:
    """Registers the built-ins, then each allowed installed plugin. Idempotent; called at
    startup (main.py, workers/run.py) and on first use by each extension point."""
    global _loaded
    with _lock:
        if _loaded:
            return list(_status)
        from openclear.plugins import builtin

        _status.append(_register(builtin.plugin, "built-in"))
        allowed = _allowed()
        for ep in sorted(entry_points(group=ENTRY_POINT_GROUP), key=lambda e: e.name):
            source = ep.dist.name if getattr(ep, "dist", None) else ep.value
            if ep.name not in allowed:
                _status.append(PluginInfo(name=ep.name, version="", source=source, status="not allowed",
                                          error="Installed but not listed in OPENCLEAR_PLUGINS"))
                continue
            try:
                plugin = ep.load()
            except Exception as exc:  # noqa: BLE001
                _status.append(PluginInfo(name=ep.name, version="", source=source, status="failed", error=f"{type(exc).__name__}: {exc}"))
                logger.exception("Plugin %s failed to import", ep.name)
                continue
            _status.append(_register(plugin, source))
        for info in _status:
            if info.status != "loaded":
                logger.warning("Plugin %s %s: %s", info.name, info.status, info.error)
        loaded = {info.name for info in _status if info.status == "loaded"}
        missing = sorted(_names(get_settings().plugins_required) - loaded)
        if missing:
            problems = "; ".join(f"{i.name}: {i.status} ({i.error})" for i in _status if i.name in missing) or "not installed"
            _reset()
            raise PluginError(f"Required plugins didn't load: {', '.join(missing)} ({problems})")
        _loaded = True
        return list(_status)


def status() -> list[PluginInfo]:
    return load()


def is_builtin(point: str, key) -> bool:
    load()
    return registry.origins.get((point, key)) == BUILTIN_NAME


def bank_choices() -> list[dict]:
    """The plugin-provided entries a bank can turn on, for Admin → Plugins."""
    load()
    return [
        {"point": point, "key": key, "label": label(key), "plugin": registry.origins.get((point, key), "?")}
        for point in BANK_CHOICE_POINTS
        for key in getattr(registry, point)
        if registry.origins.get((point, key)) != BUILTIN_NAME
    ]


def registry_rules(network_code: str) -> list:
    load()
    return [r for r in registry.matching_rules.values() if r.network == network_code]


def registry_features(network_code: str) -> list:
    load()
    return [f for f in registry.scoring_features.values() if f.network == network_code]


def registry_has(point: str) -> bool:
    load()
    return bool(getattr(registry, point))


def lookup(point: str, key, *, what: str):
    """The registered entry for `key` at an extension point, loading plugins first."""
    load()
    table = getattr(registry, point)
    if key not in table:
        raise ValueError(f"Unknown {what}: {key!r} (registered: {', '.join(map(str, table)) or 'none'})")
    return table[key]


def _reset() -> None:
    global registry, _loaded
    registry = Registry()
    _status.clear()
    _loaded = False


def reset_for_tests() -> None:
    with _lock:
        _reset()
