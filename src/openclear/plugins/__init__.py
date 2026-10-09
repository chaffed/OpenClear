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
names; installing a package alone activates nothing). A plugin that isn't allowed, needs
another plugin API version, fails to load, or claims a name something else already has is
reported (Admin → Plugins, and the log) and skipped; the app still starts. OpenClear's own
built-in network, OCR, notification, storage, scoring and import-format code registers
the same way (plugins/builtin.py).

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
)


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
        self._current: PluginInfo | None = None
        self._added: list[tuple[str, Any]] = []
        self._networks_pending: list[Any] = []

    def _add(self, point: str, key, value) -> None:
        table = getattr(self, point)
        if key in table:
            raise PluginError(f"{point} already has {key!r}")
        table[key] = value
        self._added.append((point, key))
        if self._current is not None:
            label = f"{key[0].value}/{key[1]}" if isinstance(key, tuple) else str(key)
            self._current.provides.append(f"{point}: {label}")

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


registry = Registry()
_status: list[PluginInfo] = []
_loaded = False
_lock = threading.Lock()


def _allowed() -> set[str]:
    return {name.strip() for name in get_settings().plugins.split(",") if name.strip()}


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

    registry._current, registry._added, registry._networks_pending = info, [], []
    try:
        plugin.register(registry)
    except Exception as exc:  # noqa: BLE001 -- a broken plugin is reported, not fatal
        info.status, info.error, info.provides = "failed", f"{type(exc).__name__}: {exc}", []
        for point, key in registry._added:
            getattr(registry, point).pop(key, None)
        logger.exception("Plugin %s failed to register", info.name)
    else:
        for adapter in registry._networks_pending:
            register_adapter(adapter)
    finally:
        registry._current, registry._added, registry._networks_pending = None, [], []
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
        _loaded = True
        return list(_status)


def status() -> list[PluginInfo]:
    return load()


def lookup(point: str, key, *, what: str):
    """The registered entry for `key` at an extension point, loading plugins first."""
    load()
    table = getattr(registry, point)
    if key not in table:
        raise ValueError(f"Unknown {what}: {key!r} (registered: {', '.join(map(str, table)) or 'none'})")
    return table[key]


def reset_for_tests() -> None:
    global registry, _loaded
    with _lock:
        registry = Registry()
        _status.clear()
        _loaded = False
