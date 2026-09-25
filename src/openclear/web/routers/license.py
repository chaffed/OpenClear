# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Serves the repo's own LICENSE file (AGPL-3.0-or-later) in-app, linked from base.html's
footer on every page, logged in or not."""

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from openclear.web.deps import render_template

router = APIRouter(prefix="/ui", tags=["web-license"])

_LICENSE_PATH = Path(__file__).resolve().parents[4] / "LICENSE"


@router.get("/license")
def view_license(request: Request) -> HTMLResponse:
    return render_template(request, "license.html", ctx=None, license_text=_LICENSE_PATH.read_text())
