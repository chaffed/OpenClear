# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Serves the repo's own LICENSE file (AGPL-3.0-or-later) in-app, linked from base.html's
footer on every page, logged in or not, and the third-party notices beside it
(THIRD_PARTY_NOTICES.md; plans/THIRD_PARTY_PLAN.md TP-D3)."""

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from openclear.web.deps import render_template

router = APIRouter(prefix="/ui", tags=["web-license"])

_LICENSE_PATH = Path(__file__).resolve().parents[4] / "LICENSE"
_NOTICES_PATH = Path(__file__).resolve().parents[4] / "THIRD_PARTY_NOTICES.md"


@router.get("/license")
def view_license(request: Request) -> HTMLResponse:
    # Found missing from the Docker image by the first ZAP scan (1.25): never a 500 for it.
    license_text = _LICENSE_PATH.read_text() if _LICENSE_PATH.is_file() else None
    return render_template(request, "license.html", ctx=None, license_text=license_text)


@router.get("/license/third-party")
def view_notices(request: Request) -> HTMLResponse:
    """The license texts and copyright notices of the components OpenClear includes, which their
    licenses ask to travel with it. Like LICENSE, never a 500 if the image left the file out."""
    notices = _NOTICES_PATH.read_text() if _NOTICES_PATH.is_file() else None
    return render_template(request, "license_notices.html", ctx=None, notices=notices)
