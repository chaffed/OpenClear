# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

from fastapi import APIRouter, Depends, Form
from fastapi.responses import HTMLResponse
from markupsafe import escape

from openclear.db.tenancy import TenantContext
from openclear.services import message_content
from openclear.web.deps import get_web_context
from openclear.web.rate_limit import rate_limit
from openclear.web.templates import _render_markdown

router = APIRouter(prefix="/ui", tags=["web-markdown-preview"])


@router.post("/markdown-preview")
def markdown_preview(
    text: str = Form(""),
    _ctx: TenantContext = Depends(get_web_context),
    _rate_limit: None = Depends(rate_limit("markdown_preview", limit_setting="markdown_preview_rate_limit_per_minute")),
) -> HTMLResponse:
    """Live preview for templates/_macros/markdown_editor.html -- runs the exact same
    validate-and-normalize (services/message_content.py) + sanitize-and-render
    (web/templates.py::render_markdown) pipeline the real save path uses, so what's shown
    here is exactly what will actually render, not a second, potentially-divergent
    client-side Markdown implementation -- including catching an oversized/invalid
    embedded image before the user even submits the form. No CSRF check: this has no
    side effects at all (never writes anything, purely a stateless render), same
    reasoning a GET would get if the payload weren't too large for a query string. Gated
    by simply being logged in -- rendering arbitrary text to sanitized HTML has no
    authorization concern of its own beyond that -- but that's meaningless for a public
    demo tenant whose credentials are public, hence the extra per-IP rate limit on top of
    the global one every route already gets (main.py): this endpoint runs Pillow
    decode/re-encode per call and has no other cost guard of its own."""
    try:
        normalized = message_content.validate_and_normalize_message(text)
    except message_content.InvalidMessageContent as exc:
        return HTMLResponse(f'<p class="flash flash-error">{escape(str(exc))}</p>')

    rendered = str(_render_markdown(normalized))
    return HTMLResponse(rendered or '<p class="text-muted">Nothing to preview yet.</p>')
