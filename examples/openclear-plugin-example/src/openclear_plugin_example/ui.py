# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""The example plugin's page additions (UI slots; docs/PLUGINS.md), one of each kind:

- a panel on the exception page: a "vendor score" for the check, with an action to refresh it;
- a row action on the exception queue: look the item up in the vendor's records;
- a toolbar button on the exception queue: export for the vendor;
- a page in the menu: the vendor score report;
- a control-panel tile leading to that page.

A real plugin would ask its vendor; this one makes a score up from the amount. Its own routes
are mounted at /ui/plugins/example/, and its JavaScript and CSS at /static/plugins/example/."""

from html import escape
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import PlainTextResponse, RedirectResponse

from openclear.plugins.api import (
    SlotAction,
    SlotContent,
    UiSlot,
    database,
    get_exception,
    record_action,
    render_page,
    require_web_permission,
    source_item,
    verify_csrf,
)

STATIC = Path(__file__).parent / "static"
router = APIRouter()
_read = require_web_permission("exception:read")


def vendor_score(amount) -> int:
    """0 to 100, higher is riskier: round and large amounts score higher."""
    cents = int(round(float(amount or 0) * 100))
    score = min(60, cents // 50_000) + (30 if cents % 10_000 == 0 else 0)
    return min(100, score + 10)


def _amount(session, item):
    source = source_item(session, item)
    return getattr(source, "presented_amount", None) or getattr(source, "amount", None)


def exception_panel(context) -> SlotContent | None:
    item = get_exception(context.session, context.ctx, context.values.get("exception_id"))
    if item is None:
        return None
    score = vendor_score(_amount(context.session, item))
    return SlotContent(
        title="Vendor score",
        html=f'<p><span class="example-score">{score}</span> out of 100, from the example vendor.</p>'
             '<p class="hint">Higher is riskier. Round and large amounts score higher.</p>',
        actions=(SlotAction("Refresh the score", f"/ui/plugins/example/exceptions/{item.id}/refresh", method="post"),),
    )


def exception_row_action(context) -> SlotContent:
    return SlotContent(title="Vendor lookup",
                       actions=(SlotAction("Vendor lookup", f"/ui/plugins/example/lookup?exception={context.values['exception_id']}"),))


def exceptions_toolbar(context) -> SlotContent:
    return SlotContent(title="Vendor export", actions=(SlotAction("Vendor export", "/ui/plugins/example/export"),))


def report_page(context) -> SlotContent:
    return SlotContent(title="Vendor score report",
                       html="<p>The example vendor scores each exception from its amount. Open an exception to see its score.</p>")


def settings_tile(context) -> SlotContent:
    return SlotContent(title="Example vendor", html="Vendor scores for exceptions, from the example plugin.", state="Connected",
                       actions=(SlotAction("Open", "/ui/plugin-pages/example_report"),))


SLOTS = (
    UiSlot("example_vendor_score", "exception.detail.panel", exception_panel, permission="exception:read",
           scripts=("example.js",), styles=("example.css",)),
    UiSlot("example_vendor_lookup", "exception.row.action", exception_row_action, permission="exception:read"),
    UiSlot("example_vendor_export", "exceptions.toolbar", exceptions_toolbar, permission="exception:read"),
    UiSlot("example_report", "nav.page", report_page, permission="exception:read", label="Vendor scores"),
    UiSlot("example_tile", "settings.tile", settings_tile),
)


@router.post("/exceptions/{exception_id}/refresh")
def refresh(exception_id: str, db=Depends(database), ctx=Depends(_read), _csrf: None = Depends(verify_csrf)):
    item = get_exception(db, ctx, exception_id)
    if item is None:
        return PlainTextResponse("Not found", status_code=404)
    record_action(db, ctx, action="example.score_refreshed", summary=f"Refreshed the example vendor score of exception {item.id}",
                  resource_id=item.id)
    db.commit()
    return RedirectResponse(f"/ui/exceptions/{item.id}?flash=Vendor+score+refreshed.", status_code=303)


@router.get("/lookup")
def lookup(request: Request, exception: str = "", db=Depends(database), ctx=Depends(_read)):
    item = get_exception(db, ctx, exception)
    if item is None:
        return render_page(request, ctx, title="Vendor lookup", html="<p>No such exception.</p>", status_code=404)
    score = vendor_score(_amount(db, item))
    return render_page(request, ctx, title="Vendor lookup",
                       html=f"<p>The example vendor has no record against this item. Its score is {score}.</p>"
                            f'<p><a href="/ui/exceptions/{escape(str(item.id))}">Back to the exception</a></p>')


@router.get("/export")
def export(ctx=Depends(_read)):
    return PlainTextResponse("exception_id,vendor_score\n", media_type="text/csv",
                             headers={"Content-Disposition": 'attachment; filename="vendor-export.csv"'})
