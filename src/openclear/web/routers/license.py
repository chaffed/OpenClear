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
    # Found missing from the Docker image by the first ZAP scan (1.25): never a 500 for it.
    license_text = _LICENSE_PATH.read_text() if _LICENSE_PATH.is_file() else None
    return render_template(request, "license.html", ctx=None, license_text=license_text)
