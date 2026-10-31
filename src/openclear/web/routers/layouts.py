# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Settings → File layouts (UX_PLAN.md U1, PAID_ITEM_LAYOUTS_PLAN.md; services/import_layout_service.py):
the list, and the builder, for issued-check files (issued_item:write) and the bank's posted-check
files (paid_item:write, bank staff only). The builder works from a sample file: it shows the first rows, a
field is assigned to each column (or a start and length for fixed-width), and the parsed
result is previewed before saving. The sample travels with the form (base64), never
stored. It works without JavaScript; static/js/layout-builder.js adds picking fixed-width
positions by selecting text in the sample."""

import base64
import binascii
import uuid

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from openclear import storage
from openclear.bulk_import import layouts
from openclear.db.session import get_db
from openclear.db.tenancy import TenantContext
from openclear.domain.bulk_upload_file import BulkUploadKind
from openclear.services import audit_log_service, bulk_upload_file_service, customer_service, import_layout_service
from openclear.web.deps import WebForbidden, WebNotFound, render_template, require_any_web_permission
from openclear.web.redirects import see_other
from openclear.web.security import verify_csrf
from openclear.web.errors import shown

router = APIRouter(prefix="/ui/layouts", tags=["web-layouts"])
_ANY = require_any_web_permission(*import_layout_service.PERMISSION.values())
MAX_SAMPLE_BYTES = 2_000_000
TEXT_SAMPLE_BYTES = 64_000


def _customers(db: Session, ctx: TenantContext) -> list:
    return [] if ctx.customer_id is not None else customer_service.list_customers(db, ctx.tenant_id)


def _kinds(ctx: TenantContext) -> list[str]:
    """The kinds of layout this person works with: posted-check layouts are the bank's (PIL-D2)."""
    return [kind for kind, permission in import_layout_service.PERMISSION.items()
            if permission in ctx.permissions and (kind == layouts.ISSUED_ITEMS or ctx.customer_id is None)]


def _require_kind(ctx: TenantContext, kind: str | None) -> str:
    kinds = _kinds(ctx)
    kind = kind or (kinds[0] if kinds else layouts.ISSUED_ITEMS)
    if kind not in kinds:
        raise WebForbidden()
    return kind


@router.get("")
def list_layouts(request: Request, kind: str | None = None, db: Session = Depends(get_db), ctx: TenantContext = Depends(_ANY)) -> HTMLResponse:
    kind = _require_kind(ctx, kind)
    rows = import_layout_service.visible(db, ctx.tenant_id, ctx.customer_id, kind)
    names = {c.id: c.name for c in _customers(db, ctx)}
    return render_template(request, "layouts/list.html", ctx=ctx, kind=kind, kinds=_kinds(ctx), layouts=rows, customer_names=names,
                           templates_=layouts.templates_for(kind), file_types=layouts.FILE_TYPES,
                           editable=lambda layout: import_layout_service.editable(layout, ctx.customer_id))


_UPLOAD_KIND = {layouts.ISSUED_ITEMS: BulkUploadKind.ISSUED_ITEMS, layouts.PAID_ITEMS: BulkUploadKind.PAID_ITEMS}


def _sample_from_upload(db: Session, ctx: TenantContext, upload_id: str, kind: str) -> tuple[str, bytes]:
    try:
        record = bulk_upload_file_service.get_uploaded_file(db, ctx.tenant_id, uuid.UUID(upload_id), ctx.customer_id)
    except ValueError:
        record = None
    if record is None or record.kind != _UPLOAD_KIND[kind]:
        raise WebNotFound()
    return record.original_filename, storage.read(record.storage_path)[:MAX_SAMPLE_BYTES]


def _keep(filename: str, content: bytes) -> tuple[str, bytes]:
    """What of a sample the form carries: an Excel workbook whole (it can't be cut), a text
    file's first 64 KB."""
    if filename.lower().endswith((".xlsx", ".xls")):
        if len(content) > MAX_SAMPLE_BYTES:
            raise layouts.LayoutError("That workbook is too big for a sample; save its first rows as a smaller file.")
        return filename, content
    cut = content[:TEXT_SAMPLE_BYTES]
    return filename, cut[: cut.rfind(b"\n") + 1] if len(content) > TEXT_SAMPLE_BYTES and b"\n" in cut else cut


@router.get("/new")
def new_layout(request: Request, kind: str | None = None, template: str | None = None, from_upload: str | None = None,
               db: Session = Depends(get_db), ctx: TenantContext = Depends(_ANY)) -> HTMLResponse:
    kind = _require_kind(ctx, kind)
    templates_ = layouts.templates_for(kind)
    settings: dict = dict(templates_[template].settings) if template in templates_ else {"file_type": "delimited", "has_header": True}
    settings["kind"] = kind
    name = templates_[template].name if template in templates_ else ""
    sample_name, sample = None, None
    if from_upload:
        sample_name, sample = _keep(*_sample_from_upload(db, ctx, from_upload, kind))
        settings = {**layouts.guess(sample_name, sample), "kind": kind, **{k: v for k, v in settings.items() if k == "columns" and template}}
    return _render(request, db, ctx, layout=None, name=name, owner=ctx.customer_id, settings=settings, sample_name=sample_name,
                   sample=sample, is_default=False)


@router.get("/{layout_id}")
def edit_layout(request: Request, layout_id: uuid.UUID, db: Session = Depends(get_db), ctx: TenantContext = Depends(_ANY)) -> HTMLResponse:
    layout = _editable(db, ctx, layout_id)
    return _render(request, db, ctx, layout=layout, name=layout.name, owner=layout.customer_id,
                   settings={**layout.settings, "kind": layout.kind}, sample_name=None, sample=None, is_default=layout.is_default)


def _editable(db: Session, ctx: TenantContext, layout_id: uuid.UUID):
    try:
        layout = import_layout_service.get(db, ctx.tenant_id, layout_id, ctx.customer_id)
    except import_layout_service.LayoutNotFound:
        raise WebNotFound() from None
    _require_kind(ctx, layout.kind)
    if not import_layout_service.editable(layout, ctx.customer_id):
        raise WebForbidden()
    return layout


def _sample_view(settings: dict, sample_name: str | None, sample: bytes | None) -> tuple[dict | None, list, str | None, object]:
    """(the sample as the layout reads it, the parsed preview, a problem reading it, the
    trailer check for a posted-check layout with a trailer)."""
    if not sample:
        return None, [], None, None
    try:
        partial = layouts.clean_settings(settings, complete=False)
        header, rows = layouts.grid(partial, sample_name or "sample", sample)
    except layouts.LayoutError as exc:
        return None, [], str(exc), None
    view: dict = {"header": header, "fixed": partial["file_type"] == "fixed"}
    if view["fixed"]:
        view["lines"] = rows[: layouts.MAX_SAMPLE_ROWS]
        view["width"] = max((len(line) for line in view["lines"]), default=0)
    else:
        width = max([len(header or [])] + [len(r) for r in rows[: layouts.MAX_SAMPLE_ROWS]])
        view["columns"] = list(range(width))
        view["rows"] = [list(r) + [""] * (width - len(r)) for r in rows[: layouts.MAX_SAMPLE_ROWS]]
    try:
        complete = layouts.clean_settings(settings)
        preview = layouts.preview(complete, sample_name or "sample", sample)
        trailer = layouts.check_trailer(complete, sample_name or "sample", sample)
    except layouts.LayoutError as exc:
        return view, [], str(exc), None
    return view, preview, None, trailer


def _render(request, db, ctx, *, layout, name, owner, settings, sample_name, sample, is_default, error=None, status_code=200):
    kind = layouts.kind_of(settings)
    view, preview, problem, trailer = _sample_view(settings, sample_name, sample)
    assigned = {}
    for field, ref in (settings.get("columns") or {}).items():
        if view and not view["fixed"]:
            if "position" in ref:
                assigned[ref["position"] - 1] = field
            elif view["header"]:
                for index, title in enumerate(view["header"]):
                    if title.strip().lower() == ref["name"].strip().lower():
                        assigned[index] = field
    return render_template(
        request, "layouts/form.html", ctx=ctx, layout=layout, layout_name=name, owner=owner, settings=settings, is_default=is_default,
        sample_name=sample_name, sample_b64=base64.b64encode(sample).decode() if sample else "", view=view, preview=preview,
        problem=problem, assigned=assigned, error=error, status_code=status_code,
        customers=_customers(db, ctx) if kind == layouts.ISSUED_ITEMS else [], kind=kind, noun=import_layout_service.NOUN[kind],
        fields=layouts.fields_for(kind), required=layouts.REQUIRED if kind == layouts.ISSUED_ITEMS else layouts.PAID_REQUIRED,
        file_types=layouts.FILE_TYPES, amount_formats=layouts.AMOUNT_FORMATS, date_formats=layouts.DATE_FORMATS,
        delimiters=layouts.DELIMITERS, trailer=trailer, onus_modes=layouts.ONUS_MODES, onus_orders=layouts.ONUS_ORDERS,
        trailer_matches=layouts.TRAILER_MATCHES,
    )


def _settings_from_form(form, header: list[str] | None, kind: str) -> dict:
    """The layout's settings from the builder's fields: a field chosen above a sample column
    (assign_<n>), or typed (col_<field>: a column name or number), or start_/length_ for
    fixed-width; and a posted-check layout's on-us, transaction code, routing and trailer
    settings."""
    fields = layouts.fields_for(kind)
    file_type = form.get("file_type", "delimited")
    delimiter = form.get("delimiter", ",")
    if delimiter == "other":
        delimiter = form.get("delimiter_other", "") or ","
    raw = {
        "kind": kind, "file_type": file_type, "delimiter": delimiter, "has_header": form.get("has_header") == "true", "sheet": form.get("sheet", ""),
        "skip_rows": form.get("skip_rows", "0"), "skip_footer": form.get("skip_footer", "0"), "line_prefix": form.get("line_prefix", ""),
        "amount_format": form.get("amount_format", "decimal"), "date_format": form.get("date_format", "auto"),
        "void_values": form.get("void_values", ""), "strip_zeros": form.getlist("strip_zeros"),
    }
    columns: dict = {}
    for key, value in form.items():
        if key.startswith("assign_") and value in fields:
            index = int(key.removeprefix("assign_"))
            columns[value] = {"name": header[index]} if raw["has_header"] and header and index < len(header) and header[index] else {"position": index + 1}
    for field in fields:
        typed = (form.get(f"col_{field}") or "").strip()
        if typed and field not in columns:
            columns[field] = {"position": int(typed)} if typed.isdigit() else {"name": typed}
    raw["columns"] = columns
    raw["positions"] = {field: {"start": form.get(f"start_{field}", ""), "length": form.get(f"length_{field}", "")} for field in fields
                        if (form.get(f"start_{field}") or "").strip()}
    if kind == layouts.PAID_ITEMS:
        raw |= {
            "onus_mode": form.get("onus_mode", "separator"), "onus_separator": form.get("onus_separator", ""),
            "onus_order": form.get("onus_order", "account_serial"),
            "onus_account": {"start": form.get("onus_account_start", ""), "length": form.get("onus_account_length", "")},
            "onus_serial": {"start": form.get("onus_serial_start", ""), "length": form.get("onus_serial_length", "")},
            "debit_values": form.get("debit_values", ""), "routing_numbers": form.get("routing_numbers", ""),
            "trailer": {key: form.get(f"trailer_{key}", "") for key in (
                "match", "prefix", "count_start", "count_length", "total_start", "total_length", "count_column", "total_column")},
        }
    return raw


async def _submitted(request: Request, db: Session, ctx: TenantContext, layout) -> HTMLResponse | RedirectResponse:
    form = await request.form()
    kind = layout.kind if layout is not None else _require_kind(ctx, form.get("kind") or None)
    sample_name, sample = form.get("sample_name") or None, None
    upload = form.get("sample")
    try:
        if upload is not None and hasattr(upload, "read") and getattr(upload, "filename", None):
            content = await upload.read()
            if content:
                sample_name, sample = _keep(upload.filename or "sample.csv", content[:MAX_SAMPLE_BYTES + 1])
        if sample is None and form.get("sample_b64"):
            sample = base64.b64decode(form["sample_b64"], validate=True)
    except (layouts.LayoutError, binascii.Error) as exc:
        sample = None
        error = str(exc) if isinstance(exc, layouts.LayoutError) else "The sample couldn't be read; choose it again."
        return _render(request, db, ctx, layout=layout, name=form.get("name", ""), owner=ctx.customer_id,
                       settings={"kind": kind, "file_type": "delimited"},
                       sample_name=None, sample=None, is_default=False, error=error, status_code=422)

    header = None
    if sample is not None:
        try:
            header = layouts.grid(layouts.clean_settings(_settings_from_form(form, None, kind), complete=False), sample_name or "sample",
                                  sample)[0]
        except layouts.LayoutError:
            header = None
    raw = _settings_from_form(form, header, kind)
    owner = ctx.customer_id
    if ctx.customer_id is None and form.get("owner") and kind == layouts.ISSUED_ITEMS:
        try:
            owner = uuid.UUID(form["owner"])
        except ValueError:
            owner = None
        if owner is not None and customer_service.get_customer(db, ctx.tenant_id, owner) is None:
            owner = None
    is_default = form.get("is_default") == "true"
    name = form.get("name", "")
    if form.get("action") != "save":
        return _render(request, db, ctx, layout=layout, name=name, owner=owner, settings=raw, sample_name=sample_name, sample=sample,
                       is_default=is_default)
    try:
        saved = import_layout_service.save(db, ctx.tenant_id, layout=layout, name=name, owner_customer_id=owner, raw_settings=raw,
                                           is_default=is_default, user_id=ctx.user_id, kind=kind)
    except layouts.LayoutError as exc:
        db.rollback()
        return _render(request, db, ctx, layout=layout, name=name, owner=owner, settings=raw, sample_name=sample_name, sample=sample,
                       is_default=is_default, error=shown(exc), status_code=422)
    audit_log_service.record_action(
        db, ctx.tenant_id, actor_user_id=ctx.user_id, channel="web", action="import_layout.save",
        summary=f"{'Changed' if layout else 'Added'} the {import_layout_service.NOUN[kind]} {saved.name!r}", resource_type="import_layout",
        resource_id=saved.id)
    db.commit()
    return see_other(f"/ui/layouts?kind={kind}", flash='Layout saved.')


@router.post("")
async def create_layout(request: Request, db: Session = Depends(get_db), ctx: TenantContext = Depends(_ANY),
                        _csrf: None = Depends(verify_csrf)):
    return await _submitted(request, db, ctx, None)


@router.post("/{layout_id}")
async def update_layout(request: Request, layout_id: uuid.UUID, db: Session = Depends(get_db),
                        ctx: TenantContext = Depends(_ANY), _csrf: None = Depends(verify_csrf)):
    return await _submitted(request, db, ctx, _editable(db, ctx, layout_id))


@router.post("/{layout_id}/delete")
def delete_layout(layout_id: uuid.UUID, db: Session = Depends(get_db), ctx: TenantContext = Depends(_ANY),
                  _csrf: None = Depends(verify_csrf)) -> RedirectResponse:
    layout = _editable(db, ctx, layout_id)
    name, kind = layout.name, layout.kind
    import_layout_service.delete(db, layout)
    audit_log_service.record_action(db, ctx.tenant_id, actor_user_id=ctx.user_id, channel="web", action="import_layout.delete",
                                    summary=f"Deleted the {import_layout_service.NOUN[kind]} {name!r}", resource_type="import_layout",
                                    resource_id=layout_id)
    db.commit()
    return see_other(f"/ui/layouts?kind={kind}", flash='Layout deleted. Uploads that used it keep their copy.')
