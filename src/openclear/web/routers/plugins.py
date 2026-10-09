# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Admin → Plugins: what's loaded, from where, and what each provides (PLUGIN_PLAN.md)."""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from openclear import plugins
from openclear.db.tenancy import TenantContext
from openclear.web.deps import render_template, require_web_permission

router = APIRouter(prefix="/ui/admin/plugins", tags=["web-plugins"])


@router.get("")
def list_plugins(request: Request, ctx: TenantContext = Depends(require_web_permission("admin:manage"))) -> HTMLResponse:
    return render_template(
        request, "admin/plugins.html", ctx=ctx, plugins=plugins.status(), api_version=plugins.PLUGIN_API_VERSION,
    )
