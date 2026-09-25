# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Assembles a downloadable PDF from the same topic templates the interactive
`/ui/docs/*` views already render, for web/routers/docs.py's two PDF routes.

`weasyprint` is deliberately never imported at module scope anywhere reachable during
normal app startup -- only lazily, inside `weasyprint_usable()` and `render_doc_pdf()` --
so a host missing it (or its system Pango/Cairo/GLib libraries) still runs every other
route fine; PDF export is the one feature that degrades. `import weasyprint` can raise
`OSError` (not just `ImportError`) when a native lib isn't on the dynamic loader path,
which is why both are caught."""

import mimetypes
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from openclear.web.templates import templates

if TYPE_CHECKING:
    from openclear.web.routers.docs import DocPage

_STATIC_DIR = Path(__file__).parent.parent / "static"
_DOCS_SCREENSHOTS_DIR = Path(__file__).parent.parent.parent.parent / "docs" / "screenshots"
_PDF_CSS_PATH = _STATIC_DIR / "css" / "docs-pdf.css"

# Homebrew's own lib directory (Apple Silicon vs Intel) is never on the dynamic linker's
# default search path, so even with Pango/Cairo/GLib genuinely installed via `brew
# install pango`, WeasyPrint's cffi-based `dlopen('libgobject-2.0-0', ...)` still raises
# OSError -- confirmed by hands-on reproduction, not assumed. cffi's dlopen reads
# DYLD_FALLBACK_LIBRARY_PATH from the process environment at call time, so setting it
# here (once, at module import -- before either function below's lazy `import
# weasyprint`) is sufficient; no shell-level `export` or relaunch needed. Only applies on
# macOS -- Linux package managers install into the standard ldconfig search path already,
# and this would be a no-op there anyway since neither path exists.
if sys.platform == "darwin":
    _brew_lib_dirs = [p for p in ("/opt/homebrew/lib", "/usr/local/lib") if Path(p).is_dir()]
    if _brew_lib_dirs:
        _existing = os.environ.get("DYLD_FALLBACK_LIBRARY_PATH", "")
        _combined = ":".join([_existing, *_brew_lib_dirs]) if _existing else ":".join(_brew_lib_dirs)
        os.environ["DYLD_FALLBACK_LIBRARY_PATH"] = _combined


def weasyprint_usable() -> bool:
    try:
        import weasyprint  # noqa: F401
    except (ImportError, OSError):
        return False
    return True


def _openclear_url_fetcher(url: str):
    """Resolves the doc templates' `/static/docs-screenshots/...` and `/static/...`
    image URLs straight to their real filesystem locations (mirroring main.py's own two
    StaticFiles mounts) instead of WeasyPrint making an HTTP request back to the app's
    own server mid-request -- avoids an unnecessary self-referential network hop.

    Returns a `URLFetcherResponse` (not the older `{"filename": ...}` dict shape some
    WeasyPrint docs/examples still show) -- confirmed by hands-on testing that the dict
    adapter in this installed version no longer recognizes a bare `filename` key at all
    (it only reads `file_obj`/`string`), silently producing an empty body and making
    every embedded image fail to render with no visible error until the PDF is actually
    opened. Anything outside the two known static prefixes raises, which WeasyPrint's own
    caller logs and skips (same graceful degradation as a genuinely broken image), rather
    than reaching for a real network fetch this app's doc templates never need."""
    from weasyprint.urls import URLFetcherResponse

    path = urlsplit(url).path
    if path.startswith("/static/docs-screenshots/"):
        local = _DOCS_SCREENSHOTS_DIR / path.removeprefix("/static/docs-screenshots/")
    elif path.startswith("/static/"):
        local = _STATIC_DIR / path.removeprefix("/static/")
    else:
        raise ValueError(f"unsupported URL for doc PDF export: {url}")

    mime_type, _ = mimetypes.guess_type(str(local))
    return URLFetcherResponse(url=url, body=local.read_bytes(), headers={"Content-Type": mime_type or "application/octet-stream"})


def render_doc_pdf(
    *,
    pages: "list[DocPage]",
    template_dir: str,
    base_path: str,
    title: str,
    subtitle: str,
    tenant_name: str,
    summary_counts: list[tuple[str, int]] | None = None,
) -> bytes:
    import weasyprint

    topic_fragments = []
    for page in pages:
        fragment = templates.env.get_template(f"{template_dir}/{page.slug}.html").render(
            extend_from="docs/_pdf_fragment.html",
            page=page,
            pages=pages,
            base_path=base_path,
            ctx=None,
            pdf_mode=True,
        )
        topic_fragments.append(f'<section class="pdf-topic">{fragment}</section>')
    body_html = "\n".join(topic_fragments)

    summary_html = None
    if summary_counts is not None:
        summary_html = templates.env.get_template("docs/_pdf_summary.html").render(counts=summary_counts)

    document_html = templates.env.get_template("docs/_pdf_document.html").render(
        title=title,
        subtitle=subtitle,
        tenant_name=tenant_name,
        generated_at=datetime.now(timezone.utc).strftime("%B %d, %Y"),
        pdf_css=_PDF_CSS_PATH.read_text(),
        body_html=body_html,
        summary_html=summary_html,
    )

    return weasyprint.HTML(
        string=document_html, base_url="http://openclear.local/", url_fetcher=_openclear_url_fetcher
    ).write_pdf()
