# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Which plugin entries each bank uses (PLUGIN_PLAN.md PL2). For the extension points that
are a bank's choice (plugins.BANK_CHOICE_POINTS: import formats, decision layouts), a
plugin's entry is off for every bank until that bank's admin turns it on. The operator
installing and allowing a plugin makes it available; it doesn't put it in every bank's
hands. OpenClear's built-in entries are always on."""

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from openclear import plugins
from openclear.domain.tenant_extension import TenantExtension


class NotEnabled(ValueError):
    """The entry exists but this bank hasn't turned it on; the message is fit to show."""


def _rows(session: Session, tenant_id: uuid.UUID) -> dict[tuple[str, str], TenantExtension]:
    return {
        (row.point, row.name): row
        for row in session.execute(select(TenantExtension).where(TenantExtension.tenant_id == tenant_id)).scalars()
    }


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
    """The plugin entries this bank could use, each with whether it's on."""
    rows = _rows(session, tenant_id)
    return [{**choice, "enabled": (choice["point"], choice["label"]) in rows} for choice in plugins.bank_choices()]


def enabled(session: Session, tenant_id: uuid.UUID, point: str) -> list:
    """The keys of `point` this bank can use: the built-ins and the plugin entries it turned on."""
    rows = _rows(session, tenant_id)
    return [key for key in getattr(plugins.registry, point)
            if plugins.is_builtin(point, key) or (point, plugins.label(key)) in rows]


def set_enabled(session: Session, tenant_id: uuid.UUID, point: str, name: str, on: bool, *, user_id: uuid.UUID | None) -> bool:
    """Turns a plugin entry on or off for the bank. Returns whether anything changed."""
    if not any(c["point"] == point and c["label"] == name for c in plugins.bank_choices()):
        raise ValueError("That isn't something a plugin provides here.")
    row = _rows(session, tenant_id).get((point, name))
    if on and row is None:
        session.add(TenantExtension(tenant_id=tenant_id, point=point, name=name, enabled_by_user_id=user_id))
    elif not on and row is not None:
        session.delete(row)
    else:
        return False
    session.flush()
    return True
