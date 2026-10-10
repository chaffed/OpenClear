# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import re
from pathlib import Path
from urllib.parse import urlencode

import markdown
import nh3
from fastapi.templating import Jinja2Templates
from markupsafe import Markup

from openclear.db.tenancy import TenantContext

TEMPLATES_DIR = Path(__file__).parent.parent / "templates"
_STATIC_DIR = Path(__file__).parent.parent / "static"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def _asset_version(relative_path: str) -> int:
    """Exposed to every template as `asset_version("js/app.js")` — a cache-busting query
    param derived from the static file's own mtime, appended to its URL in base.html.
    Without this, a browser can keep serving a stale cached copy of app.js/app.css after
    a deploy (StaticFiles' response carries only Last-Modified/ETag, no Cache-Control, so
    heuristic caching can serve an old copy without even a revalidation request) — the
    query string changes automatically whenever the underlying file's mtime changes, no
    manual version bump required."""
    try:
        return int((_STATIC_DIR / relative_path).stat().st_mtime)
    except OSError:
        return 0


templates.env.globals["asset_version"] = _asset_version


def _can(ctx: TenantContext | None, permission: str) -> bool:
    """Exposed to every template as `can(ctx, "resource:action")` — checks the exact same
    ctx.permissions set the server-side route dependencies check
    (web/deps.py::require_web_permission), so a template can never show a control that
    the corresponding POST route would reject. This is cosmetic only: hiding a button
    doesn't grant anything, the route's own require_web_permission()/require_permission()
    dependency is what actually enforces it."""
    if ctx is None:
        return False
    return permission in ctx.permissions


templates.env.globals["can"] = _can


def _format_metrics(metrics: dict | None) -> str:
    """Exposed to every template as `format_metrics(m.metrics_json)` — renders an ML
    model's metrics dict (precision/recall/auc from ml/train.py, or {"error": ...} for a
    failed retrain from workers/tasks.py::_train_and_log) as a readable string instead of
    a raw Python dict repr (`{'precision': 0.5, ...}`)."""
    if not metrics:
        return "—"
    parts = []
    for key, value in metrics.items():
        if isinstance(value, dict):
            continue  # e.g. ml/train.py's "evaluation" details, shown separately
        if key in ("precision", "recall") and isinstance(value, (int, float)):
            parts.append(f"{key.capitalize()}: {value * 100:.0f}%")
        elif isinstance(value, (int, float)):
            parts.append(f"{key.upper() if key == 'auc' else key.capitalize()}: {value:.2f}")
        else:
            parts.append(f"{key.capitalize()}: {value}")
    return ", ".join(parts)


templates.env.globals["format_metrics"] = _format_metrics


# Plain-English names for the rule codes stored in ExceptionItem.exception_types
# (networks/check/types.py, networks/ach/types.py). An unknown code falls back to a
# readable version of itself, so a new rule never renders blank.
_EXCEPTION_TYPE_LABELS = {
    "duplicate_paid": "Already paid",
    "stopped": "Stop payment",
    "voided": "Voided check",
    "not_in_file": "Not in issued file",
    "amount_mismatch": "Amount mismatch",
    "payee_mismatch": "Payee mismatch",
    "stale_dated": "Stale-dated",
    "unauthorized_originator": "Unauthorized originator",
    "receiver_id_not_permitted": "Receiver not authorized",
    "amount_exceeds_limit": "Over authorized amount",
    "frequency_exceeded": "Over authorized frequency",
    "sec_code_not_permitted": "SEC code not authorized",
    "plugin_rule_failed": "A plugin's check failed (review by hand)",
}


def _exception_type_label(code: str) -> str:
    code = (code or "").strip()
    if code in _EXCEPTION_TYPE_LABELS:
        return _EXCEPTION_TYPE_LABELS[code]
    from openclear import plugins  # codes matching-rule plugins add, with the names they gave

    return plugins.registry.exception_type_labels.get(code, code.replace("_", " ").capitalize())


def _exception_type_labels(codes: str | None) -> str:
    """Registered as the `exception_types` filter: "amount_mismatch,stale_dated" →
    "Amount mismatch, Stale-dated"."""
    return ", ".join(_exception_type_label(c) for c in (codes or "").split(",") if c.strip())


_EXCEPTION_STATUS_LABELS = {
    "open": "Needs review",
    "pending_approval": "Awaiting approval",
    "pay": "Paid",
    "return": "Returned",
    "escalated": "Escalated",
    "withdrawn": "Withdrawn",
}


def _exception_status_label(status) -> str:
    value = getattr(status, "value", status) or ""
    return _EXCEPTION_STATUS_LABELS.get(value, value.replace("_", " ").capitalize())


def _fraud_risk(score: float | None) -> dict | None:
    """Exposed as `fraud_risk(item.ml_score)`. ExceptionItem.ml_score is the model's
    probability that the item should be PAID (ml/model.py; auto-disposition pays at
    >= 0.5). Reviewers think in terms of risk, and a bare "0.95" is easy to read the wrong
    way round, so this turns it into a level plus the chance it should be returned.
    None when there's no score yet."""
    if score is None:
        return None
    return_chance = max(0.0, min(1.0, 1.0 - float(score)))
    if return_chance >= 0.5:
        level, badge = "High", "badge-danger"
    elif return_chance >= 0.2:
        level, badge = "Medium", "badge-warn"
    else:
        level, badge = "Low", "badge-ok"
    return {"level": level, "badge": badge, "return_percent": round(return_chance * 100)}


templates.env.filters["exception_types"] = _exception_type_labels
templates.env.filters["exception_status"] = _exception_status_label
templates.env.globals["fraud_risk"] = _fraud_risk


def _currency(value):
    """Registered as the `currency` Jinja filter — formats a Decimal/numeric amount as
    `$1,234.56`. Returns None unchanged (rather than raising or printing "None") so
    existing `{{ x or "any" }}` fallbacks — e.g. stop_payments/list.html's nullable stop
    amount — keep working exactly as before."""
    if value is None:
        return None
    return f"${value:,.2f}"


templates.env.filters["currency"] = _currency


def _long_date(value):
    """`October 1, 2026`, the same on every platform (strftime's %-d isn't on Windows)."""
    return f"{value:%B} {value.day}, {value.year}" if value else ""


templates.env.filters["long_date"] = _long_date

# Tags a tenant/customer-authored banner or login message may use. `img` is allowed --
# but only ever a data: URI holding a small, pre-validated raster image
# (services/message_content.py validates/re-encodes it at save time; the
# _attribute_filter/_SAFE_IMAGE_DATA_URI_RE below is the render-time half of that same
# defense-in-depth, not a replacement for it) -- no raw `style`/`class`/`id` (no styling
# escape hatch out of the banner's own look).
_MARKDOWN_ALLOWED_TAGS = {
    "p", "br", "strong", "em", "a", "ul", "ol", "li",
    "h1", "h2", "h3", "h4", "h5", "h6", "code", "pre", "blockquote", "hr", "img",
}
_MARKDOWN_ALLOWED_ATTRIBUTES = {"a": {"href", "title"}, "img": {"src", "alt"}}
# nh3's own `url_schemes` is a single allowlist shared by every URL-bearing attribute --
# confirmed directly that restricting it to just {"data"} to allow images also strips the
# href off an ordinary https:// link. The per-tag split (images: data: only; links:
# http(s)/mailto only) is enforced by _attribute_filter below instead.
_MARKDOWN_ALLOWED_URL_SCHEMES = {"http", "https", "mailto", "data"}
# Deliberately narrower than services/message_content.py's own allowed MIME set would
# need to be for a NEW upload (this only needs to match what that module could have
# possibly produced) -- but written independently, not by importing that module's
# constant, so this stays correct even if a future save-path bug ever wrote something
# non-conforming: render-time is the last line of defense, not assumed to be redundant.
_SAFE_IMAGE_DATA_URI_RE = re.compile(r"^data:image/(png|jpeg|gif|webp);base64,[A-Za-z0-9+/]+=*$")


def _markdown_attribute_filter(tag: str, attr: str, value: str) -> str | None:
    if tag == "img" and attr == "src":
        return value if _SAFE_IMAGE_DATA_URI_RE.match(value) else None
    if tag == "a" and attr == "href":
        return value if value.startswith(("http://", "https://", "mailto:")) else None
    return value


def _render_markdown(text: str | None) -> Markup:
    """Exposed to templates as `{{ text|render_markdown }}` — converts tenant/customer-
    authored Markdown (login/banner messages, see db/tenancy.py::TenantContext and
    services/tenant_service.py::TenantBranding) to HTML, then strips it down to a small
    safe tag/attribute allowlist via nh3 (a maintained Rust-backed sanitizer) before
    returning it wrapped in `Markup` — the idiomatic Jinja way to hand back deliberately
    pre-sanitized HTML that autoescaping should NOT re-escape, clearer and harder to
    misuse later than sprinkling a bare `|safe` on raw, unsanitized text. This is the
    only place in the app that renders admin-authored rich text as HTML; every other use
    of admin-supplied text elsewhere still gets Jinja's normal autoescaping."""
    if not text:
        return Markup("")
    html = markdown.markdown(text)
    clean_html = nh3.clean(
        html, tags=_MARKDOWN_ALLOWED_TAGS, attributes=_MARKDOWN_ALLOWED_ATTRIBUTES,
        url_schemes=_MARKDOWN_ALLOWED_URL_SCHEMES, attribute_filter=_markdown_attribute_filter,
        link_rel="noopener noreferrer nofollow",
    )
    return Markup(clean_html)


templates.env.filters["render_markdown"] = _render_markdown


def _pager_href(request, page: int) -> str:
    """Exposed to templates as `pager_href(request, page)` — used by
    templates/_macros/pagination.html's Prev/Next links. Builds a query string with
    `page` replaced (or added), preserving every other query param already on the
    request (status filters, network filters, etc.) so paging never resets a filter."""
    params = dict(request.query_params)
    params["page"] = str(page)
    return "?" + urlencode(params)


templates.env.globals["pager_href"] = _pager_href


def _list_return(request, list_path: str) -> str:
    """Exposed to templates as `list_return(request, "/ui/issued-items")`: where "Back to
    the list" goes from a detail page. The list's own link passes `from` (its path and
    filters); it's used only if it's a safe path on that same list, so a crafted link can't
    send anyone elsewhere."""
    from openclear.web.security import safe_next_path

    candidate = safe_next_path(request.query_params.get("from"), default="")
    if candidate == list_path or candidate.startswith(list_path + "?"):
        return candidate
    return list_path


templates.env.globals["list_return"] = _list_return
