# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Plugin UI slots (MOBILE_PLAN.md R5; PLUGIN_PLAN.md PL4): places on OpenClear's pages where a
plugin adds a panel, a toolbar button, a row action, a page or a control-panel tile.

A plugin registers a `UiSlot` (plugins/api.py) naming the slot it fills; templates call
`{{ slot("exception.detail.panel", exception_id=item.id) }}`. For each slot this bank turned
on (Settings → Plugins) and the person's permissions allow, OpenClear calls the plugin's
`render(context)` and shows what it returns. The rules that keep a page safe from a plugin:

- **It always renders.** Each `render` runs in a worker thread with its own database session,
  bound to the request's bank, person and accounts (so customer scope and per-account access
  apply), and is given SLOT_BUDGET_SECONDS. One that raises, or takes longer, is skipped and
  logged (`plugin.slot.failed`); the page goes on without it.
- **Plain values in.** A slot gets ids and strings (`values`), never the request's objects,
  so a slow slot can't touch the request's session after the page has moved on.
- **Sanitised HTML out.** A slot's HTML is cleaned to SLOT_TAGS and SLOT_ATTRIBUTES (no
  scripts, styles or event handlers), so the Content Security Policy holds. A plugin's own
  JavaScript and CSS are static files (`registry.add_static`), loaded only on pages where one
  of its slots rendered.
- **Its own routes for actions.** A slot's actions point at the plugin's routes
  (`registry.add_web_router`, mounted at `/ui/plugins/<plugin>/`), which check permissions and
  CSRF and record what they do, as OpenClear's own do."""

import logging
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass, field
from typing import Any, Callable

import nh3

logger = logging.getLogger(__name__)

# Every slot, and its kind (how it shows: MOBILE_PLAN.md §3).
SLOTS = {
    "exceptions.toolbar": "toolbar",
    "issued_items.toolbar": "toolbar",
    "exception.row.action": "row_action",
    "paid_item.row.action": "row_action",
    "exception.detail.panel": "panel",
    "customer.detail.panel": "panel",
    "dashboard.panel": "panel",
    "nav.page": "page",
    "settings.tile": "tile",
}
SLOT_BUDGET_SECONDS = 0.2
PAGE_BUDGET_SECONDS = 2.0  # a whole page (nav.page) has longer than a part of one
SLOT_TAGS = {
    "a", "abbr", "b", "br", "code", "dd", "div", "dl", "dt", "em", "h3", "h4", "i", "li", "ol", "p", "small", "span", "strong",
    "table", "tbody", "td", "th", "thead", "tr", "ul",
}
SLOT_ATTRIBUTES = {"*": {"class", "title"}, "a": {"href"}, "td": {"colspan"}, "th": {"colspan", "scope"}}

_pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="plugin-slot")


@dataclass(frozen=True)
class SlotAction:
    """A button or link a slot adds. `href` is a path on this site (a plugin's own route under
    /ui/plugins/<plugin>/); a "post" action is sent as a form with the page's CSRF token."""

    label: str
    href: str
    method: str = "get"  # "get" or "post"


@dataclass(frozen=True)
class SlotContent:
    """What a slot shows. For a tile, `html` is its one-line description, `state` what it shows
    beside its name, and the first action where it leads."""

    title: str
    html: str = ""
    actions: tuple[SlotAction, ...] = ()
    state: str = ""


@dataclass(frozen=True)
class SlotContext:
    """What `render` gets: the request's TenantContext (bank, person, customer, permissions), a
    database session of its own bound to the same scope, the plain values the page passed, and
    the slot being filled."""

    ctx: Any
    session: Any
    values: dict
    slot: str


@dataclass(frozen=True)
class UiSlot:
    """A plugin's entry in one slot (SLOTS). `name` is unique across plugins; a bank turns it on
    by that name. `permission` limits it to people holding that permission. For a page
    (`nav.page`), `label` is its menu entry. `scripts` and `styles` are paths inside the
    plugin's static folder, loaded on pages where this slot rendered."""

    name: str
    slot: str
    render: Callable[[SlotContext], SlotContent | None]
    permission: str | None = None
    label: str = ""
    scripts: tuple[str, ...] = field(default=())
    styles: tuple[str, ...] = field(default=())


@dataclass(frozen=True)
class Rendered:
    """A slot's content, cleaned, with what it needs on the page."""

    entry: UiSlot
    plugin: str
    content: SlotContent
    html: str  # sanitised


def sanitise(html: str) -> str:
    return nh3.clean(html or "", tags=SLOT_TAGS, attributes=SLOT_ATTRIBUTES, url_schemes={"http", "https", "mailto"},
                     link_rel="noopener noreferrer")


def _safe_href(href: str) -> bool:
    return isinstance(href, str) and href.startswith("/") and not href.startswith("//")


def _session_factory():
    from openclear.db.session import get_session_factory
    from openclear.services import security_events

    return security_events._detached_factory or get_session_factory()


def _run(entry: UiSlot, ctx, values: dict, slot: str) -> SlotContent | None:
    from openclear.db.account_scope import bind_for_member
    from openclear.db.tenancy import bind_session_to_tenant

    session = _session_factory()()
    try:
        bind_session_to_tenant(session, ctx.tenant_id, user_id=ctx.user_id)
        bind_for_member(session, ctx.tenant_id, ctx.user_id, ctx.customer_id)
        return entry.render(SlotContext(ctx=ctx, session=session, values=dict(values), slot=slot))
    finally:
        session.rollback()  # a slot shows; it never changes anything
        session.close()


def _failed(entry: UiSlot, plugin: str, ctx, reason: str, detail: str = "") -> None:
    logger.warning("plugin.slot.failed", extra={"event": "plugin.slot.failed", "plugin": plugin, "slot": entry.slot,
                                                "slot_name": entry.name, "reason": reason, "detail": detail[:300],
                                                "tenant_id": str(ctx.tenant_id)})


def render_one(entry: UiSlot, ctx, values: dict, *, budget: float = SLOT_BUDGET_SECONDS) -> Rendered | None:
    """One entry's content, or None (nothing to show, failed, too slow, or malformed)."""
    from openclear import plugins

    plugin = plugins.registry.origins.get(("ui_slots", entry.name), "?")
    started = time.monotonic()
    future = _pool.submit(_run, entry, ctx, values, entry.slot)
    try:
        content = future.result(timeout=budget)
    except FutureTimeout:
        _failed(entry, plugin, ctx, "slow", f"no answer within {budget * 1000:.0f} ms")
        return None
    except Exception as exc:  # noqa: BLE001 -- a broken slot is skipped, never the page
        _failed(entry, plugin, ctx, "error", f"{type(exc).__name__}: {exc}")
        return None
    if content is None:
        return None
    if not isinstance(content, SlotContent) or not all(isinstance(a, SlotAction) and _safe_href(a.href) for a in content.actions):
        _failed(entry, plugin, ctx, "malformed", "render must return SlotContent whose actions point at paths on this site")
        return None
    logger.debug("plugin slot %s rendered in %.0f ms", entry.name, (time.monotonic() - started) * 1000)
    return Rendered(entry=entry, plugin=plugin, content=content, html=sanitise(content.html))


def entries_for(ctx, slot: str, enabled_names: set[str]) -> list[UiSlot]:
    """The entries in `slot` this bank turned on and this person may see."""
    from openclear import plugins

    plugins.load()
    return [e for e in plugins.registry.ui_slots.values()
            if e.slot == slot and e.name in enabled_names and (e.permission is None or e.permission in ctx.permissions)]


def enabled_names(tenant_id: uuid.UUID) -> set[str]:
    """The UI slots this bank turned on, read in a session of their own (templates have none)."""
    from openclear import plugins
    from openclear.db.tenancy import bind_session_to_tenant
    from openclear.services import plugin_service

    plugins.load()
    if not plugins.registry.ui_slots:
        return set()
    session = _session_factory()()
    try:
        bind_session_to_tenant(session, tenant_id)
        return {str(name) for name in plugin_service.enabled(session, tenant_id, "ui_slots")}
    finally:
        session.close()


def render_slot(request, ctx, slot: str, values: dict) -> list[Rendered]:
    """Every entry's content for `slot` on this request, in name order, remembering what the page
    must load for them (assets_for)."""
    if ctx is None or slot not in SLOTS:
        return []
    state = request.state if request is not None else None
    names = getattr(state, "plugin_slots_enabled", None)
    if names is None:
        names = enabled_names(ctx.tenant_id)
        if state is not None:
            state.plugin_slots_enabled = names
    if not names:
        return []
    out = [r for e in sorted(entries_for(ctx, slot, names), key=lambda e: e.name) if (r := render_one(e, ctx, values)) is not None]
    remember_assets(request, out)
    return out


def remember_assets(request, rendered: list[Rendered]) -> None:
    """Note the scripts and stylesheets these slots need, for the end of the page (assets_for)."""
    state = getattr(request, "state", None)
    if state is None or not rendered:
        return
    assets = getattr(state, "plugin_assets", [])
    for r in rendered:
        assets += [("js", f"/static/plugins/{r.plugin}/{path.lstrip('/')}") for path in r.entry.scripts]
        assets += [("css", f"/static/plugins/{r.plugin}/{path.lstrip('/')}") for path in r.entry.styles]
    state.plugin_assets = assets


def assets_for(request) -> list[tuple[str, str]]:
    """The scripts and stylesheets of the plugins whose slots rendered on this page, once each."""
    seen, out = set(), []
    for asset in getattr(request.state, "plugin_assets", []) if request is not None else []:
        if asset not in seen:
            seen.add(asset)
            out.append(asset)
    return out
