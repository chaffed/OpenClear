# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Which plugin entries each bank uses, and running them (PLUGIN_PLAN.md PL2, PL3).

For the extension points that are a bank's choice (plugins.BANK_CHOICE_POINTS: import
formats, decision layouts, matching rules, notification channels), a plugin's entry is off
for every bank until that bank's admin turns it on. The operator installing and allowing a
plugin makes it available; it doesn't put it in every bank's hands. OpenClear's built-in
entries are always on.

Running them:
- **Matching rules** run after a network's own rules, each in a savepoint. They can only
  add exception types. A rule that raises adds `plugin_rule_failed` instead, so a broken
  rule sends the item to a person rather than letting it through.
- **Scoring features** add model inputs for every bank (the shared model is trained on all
  of them). A feature that raises is left out and logged; scoring carries on.
- **Notification channels** get the same events as webhooks (services/webhook_service.emit),
  each sent by a background job, retried if the channel raises."""

import json
import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from openclear import plugins
from openclear.domain.tenant_extension import TenantExtension

logger = logging.getLogger(__name__)

RULE_FAILED = "plugin_rule_failed"
NOTIFY_JOB_KIND = "plugin.notify"


class NotEnabled(ValueError):
    """The entry exists but this bank hasn't turned it on; the message is fit to show."""


def _rows(session: Session, tenant_id: uuid.UUID) -> dict[tuple[str, str], TenantExtension]:
    cache = session.info.setdefault("tenant_extensions", {})
    if tenant_id not in cache:
        cache[tenant_id] = {
            (row.point, row.name): row
            for row in session.execute(select(TenantExtension).where(TenantExtension.tenant_id == tenant_id)).scalars()
        }
    return cache[tenant_id]


def _forget(session: Session) -> None:
    session.info.pop("tenant_extensions", None)


def is_enabled(session: Session, tenant_id: uuid.UUID, point: str, key) -> bool:
    if point not in plugins.BANK_CHOICE_POINTS or plugins.is_builtin(point, key):
        return True
    return (point, plugins.label(key)) in _rows(session, tenant_id)


def require_enabled(session: Session, tenant_id: uuid.UUID, point: str, key, *, what: str):
    """The registered entry, if it exists and this bank may use it."""
    entry = plugins.lookup(point, key, what=what)
    if not is_enabled(session, tenant_id, point, key):
        raise NotEnabled(f"The {what} {plugins.label(key)!r} isn't turned on for this organization (Admin → Plugins).")
    return entry


def choices(session: Session, tenant_id: uuid.UUID) -> list[dict]:
    """The plugin entries this bank could use, each with whether it's on (and, for a
    notification channel, its settings to fill in)."""
    rows = _rows(session, tenant_id)
    out = []
    for choice in plugins.bank_choices():
        row = rows.get((choice["point"], choice["label"]))
        entry = {**choice, "enabled": row is not None, "config_fields": [], "config": {}}
        if choice["point"] == "notification_channels":
            channel = plugins.registry.notification_channels[choice["key"]]
            entry["config_fields"] = list(channel.config_fields)
            saved = _config(row)
            entry["config"] = {f.name: ("" if f.secret else saved.get(f.name, "")) for f in channel.config_fields}
        out.append(entry)
    return out


def enabled(session: Session, tenant_id: uuid.UUID, point: str) -> list:
    """The keys of `point` this bank can use: the built-ins and the plugin entries it turned on."""
    rows = _rows(session, tenant_id)
    return [key for key in getattr(plugins.registry, point)
            if plugins.is_builtin(point, key) or (point, plugins.label(key)) in rows]


def _config(row: TenantExtension | None) -> dict[str, str]:
    if row is None or not row.config_encrypted:
        return {}
    from openclear.auth.crypto import decrypt_secret

    return json.loads(decrypt_secret(row.config_encrypted))


def set_enabled(
    session: Session, tenant_id: uuid.UUID, point: str, name: str, on: bool, *, user_id: uuid.UUID | None,
    config: dict[str, str] | None = None,
) -> bool:
    """Turns a plugin entry on or off for the bank, saving a notification channel's
    settings (a blank secret keeps the saved one). Returns whether anything changed."""
    if not any(c["point"] == point and c["label"] == name for c in plugins.bank_choices()):
        raise ValueError("That isn't something a plugin provides here.")
    row = _rows(session, tenant_id).get((point, name))
    encrypted = None
    if on and point == "notification_channels":
        encrypted = _checked_config(plugins.registry.notification_channels[name], config or {}, _config(row))
    if on and row is None:
        session.add(TenantExtension(tenant_id=tenant_id, point=point, name=name, enabled_by_user_id=user_id, config_encrypted=encrypted))
    elif on and encrypted is not None and encrypted != row.config_encrypted:
        row.config_encrypted = encrypted
    elif not on and row is not None:
        session.delete(row)
    else:
        return False
    session.flush()
    _forget(session)
    return True


def _checked_config(channel, given: dict[str, str], saved: dict[str, str]) -> str | None:
    from openclear.auth.crypto import encrypt_secret

    values = {}
    for f in channel.config_fields:
        value = (given.get(f.name) or "").strip()
        if not value and f.secret:
            value = saved.get(f.name, "")
        if f.required and not value:
            raise ValueError(f"{f.label} is required.")
        if value:
            values[f.name] = value
    return encrypt_secret(json.dumps(values, sort_keys=True)) if values else None


# --- Matching rules (PL3) ------------------------------------------------------------------


def apply_matching_rules(session: Session, network_code: str, transaction, result):
    """The network's MatchResult with the exception types the bank's matching-rule plugins
    add. Never removes one."""
    from openclear.networks.base import MatchResult

    rules = [r for r in plugins.registry_rules(network_code)
             if is_enabled(session, transaction.tenant_id, "matching_rules", r.name)]
    if not rules:
        return result
    added: list[str] = []
    for rule in rules:
        try:
            with session.begin_nested():
                codes = list(rule.check(session, transaction) or [])
            unknown = [c for c in codes if c not in rule.exception_types]
            if unknown:
                raise plugins.PluginError(f"returned undeclared exception types {unknown}")
        except Exception:  # noqa: BLE001 -- fail closed: a person looks at the item
            logger.exception("Matching rule %s failed on %s %s", rule.name, network_code, getattr(transaction, "id", "?"))
            codes = [RULE_FAILED]
        added += [c for c in codes if c not in added]
    added = [c for c in added if c not in result.exception_types]
    if not added:
        return result
    return MatchResult(matched=False, exception_types=[*result.exception_types, *added],
                       related_reference_id=result.related_reference_id)


# --- Scoring features (PL3) ----------------------------------------------------------------


def extra_features(session: Session, network_code: str, exception_item) -> dict:
    out: dict = {}
    for feature in plugins.registry_features(network_code):
        try:
            with session.begin_nested():
                values = feature.build(session, exception_item) or {}
        except Exception:  # noqa: BLE001 -- a missing input is better than no score
            logger.exception("Scoring feature %s failed", feature.name)
            continue
        for key, value in values.items():
            if isinstance(value, bool):
                value = int(value)
            if isinstance(value, (int, float, str)):
                out[f"plugin:{feature.name}:{key}"] = value
    return out


# --- Notification channels (PL3) -----------------------------------------------------------


def queue_channel_events(session: Session, tenant_id: uuid.UUID, event_type: str, data: dict) -> int:
    """Queues `event_type` to each notification channel the bank turned on that wants it,
    in the caller's transaction (like webhooks). Returns how many were queued."""
    from openclear.workers.jobs import enqueue

    if not plugins.registry_has("notification_channels"):
        return 0
    queued = 0
    for name in enabled(session, tenant_id, "notification_channels"):
        channel = plugins.registry.notification_channels[name]
        if channel.events is not None and event_type not in channel.events:
            continue
        event = {"type": event_type, "timestamp": datetime.now(timezone.utc).isoformat(), "data": data}
        enqueue(session, NOTIFY_JOB_KIND, tenant_id=tenant_id, payload={"channel": name, "event": event})
        queued += 1
    return queued


def deliver_channel_event(session: Session, tenant_id: uuid.UUID, channel_name: str, event: dict) -> None:
    """The `plugin.notify` job. Raises (so the job is retried) if the channel does."""
    from openclear.domain.tenant import Tenant
    from openclear.services.notification_service import _absolute_url

    channel = plugins.registry.notification_channels.get(channel_name)
    row = _rows(session, tenant_id).get(("notification_channels", channel_name))
    if channel is None or row is None:
        return  # the plugin was removed or the bank turned it off since
    tenant = session.get(Tenant, tenant_id)
    exception_id = (event.get("data") or {}).get("exception_id")
    channel.send(
        {**event, "bank": tenant.name if tenant else None, "link": _absolute_url(f"/ui/exceptions/{exception_id}") if exception_id else None},
        _config(row),
    )
