# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Plugin pages and routes (MOBILE_PLAN.md R5; plugins/ui_slots.py): a plugin's `nav.page` entry
shown in OpenClear's layout at /ui/plugin-pages/<name>, and each plugin's own routes and static
files mounted at /ui/plugins/<plugin>/ and /static/plugins/<plugin>/. A plugin's routes answer
only for a bank that turned on at least one of its UI slots."""

from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from openclear.db.tenancy import TenantContext
from openclear.plugins import ui_slots
from openclear.web.deps import WebNotFound, get_web_context, render_template

router = APIRouter(tags=["web-plugin-pages"])


@router.get("/ui/plugin-pages/{name}")
def plugin_page(request: Request, name: str, ctx: TenantContext = Depends(get_web_context)) -> HTMLResponse:
    entry = next((e for e in ui_slots.entries_for(ctx, "nav.page", ui_slots.enabled_names(ctx.tenant_id)) if e.name == name), None)
    if entry is None:
        raise WebNotFound()
    rendered = ui_slots.render_one(entry, ctx, dict(request.query_params), budget=ui_slots.PAGE_BUDGET_SECONDS)
    if rendered is None:
        return render_template(request, "plugins/page.html", ctx=ctx, page_title=entry.label, status_code=503,
                               page_html="<p>This page isn't available right now. Try again in a moment.</p>")
    ui_slots.remember_assets(request, [rendered])
    return render_template(request, "plugins/page.html", ctx=ctx, page_title=rendered.content.title or entry.label,
                           page_html=rendered.html, page_actions=rendered.content.actions)


def _turned_on(plugin: str):
    def check(ctx: TenantContext = Depends(get_web_context)) -> TenantContext:
        from openclear import plugins

        names = ui_slots.enabled_names(ctx.tenant_id)
        if not any(plugins.registry.origins.get(("ui_slots", n)) == plugin for n in names):
            raise WebNotFound()
        return ctx

    return check


def mount(app: FastAPI) -> None:
    """Each loaded plugin's routes and static files. Called by main.create_app after plugins
    load, before the broader /static mount."""
    from openclear import plugins

    plugins.load()
    for plugin, plugin_router in plugins.registry.web_routers.items():
        app.include_router(plugin_router, prefix=f"/ui/plugins/{plugin}", include_in_schema=False, dependencies=[Depends(_turned_on(plugin))])
    for plugin, directory in plugins.registry.static_dirs.items():
        app.mount(f"/static/plugins/{plugin}", StaticFiles(directory=directory), name=f"plugin-static-{plugin}")
