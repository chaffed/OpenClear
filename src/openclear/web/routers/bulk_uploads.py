# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import csv
import io
import uuid
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, Response, StreamingResponse
from sqlalchemy.orm import Session

from openclear import storage
from openclear.db.session import get_db
from openclear.db.tenancy import TenantContext
from openclear.domain.bulk_upload_file import BulkUploadFile, BulkUploadKind
from openclear.services import bulk_import_service, bulk_upload_file_service, bulk_upload_reversal_service, plugin_service
from openclear.web.deps import WebForbidden, WebNotFound, get_web_context, render_template
from openclear.web.redirects import see_other
from openclear.web.security import verify_csrf
from openclear.workers.jobs import jobs_queued

router = APIRouter(prefix="/ui/bulk-uploads", tags=["web-bulk-uploads"])

# The permission that gated the original upload gates viewing/downloading it later.
_PERMISSION_BY_KIND = bulk_import_service.PERMISSION_BY_KIND

# Where "back" goes from an upload's page: the list its rows landed in.
# What one row of each kind makes, for the outcome sentence (UI_PLAN.md FL5).
_NOUNS = {
    BulkUploadKind.ISSUED_ITEMS: ("issued check", "issued checks"),
    BulkUploadKind.ACH_TRANSACTIONS: ("ACH transaction", "ACH transactions"),
    BulkUploadKind.USERS: ("user", "users"),
    BulkUploadKind.ACCOUNTS: ("account", "accounts"),
    BulkUploadKind.CHECK_IMAGES: ("check image", "check images"),
    BulkUploadKind.PAID_ITEMS: ("paid check", "paid checks"),
}


def _record_details(db: Session, ctx: TenantContext, tracked: list) -> dict:
    """For each issued check an upload created: its number, amount and payee, so the list says
    what was made, not only "issued_item"."""
    from openclear.repositories.issued_item_repo import IssuedItemRepository

    ids = [t.resource_id for t in tracked if t.resource_type == "issued_item"]
    if not ids:
        return {}
    repo = IssuedItemRepository(db, ctx.tenant_id, ctx.customer_id)
    details = {}
    for resource_id in ids:
        item = repo.get(resource_id)
        if item is not None:
            details[resource_id] = f"Check {item.check_number} · ${item.amount:,.2f}" + (f" · {item.payee_name}" if item.payee_name else "")
    return details


_BACK_BY_KIND = {
    BulkUploadKind.ISSUED_ITEMS: ("/ui/issued-items", "Back to issued items"),
    BulkUploadKind.ACH_TRANSACTIONS: ("/ui/ach/transactions", "Back to ACH transactions"),
    BulkUploadKind.USERS: ("/ui/users", "Back to users"),
    BulkUploadKind.ACCOUNTS: ("/ui/accounts", "Back to accounts"),
    BulkUploadKind.CHECK_IMAGES: ("/ui/check-images", "Back to check images"),
    BulkUploadKind.PAID_ITEMS: ("/ui/paid-items", "Back to paid items"),
}

_FAILURES_SHOWN = 500

# Backing out a CHECK_IMAGES upload can create/revert paid_item and issued_item state,
# not just check_image state (see bulk_upload_reversal_service.py) — the same
# dual-permission requirement the original upload route itself uses
# (web/routers/check_images.py::_require_bulk_check_image_permissions), on top of the
# single permission above that already gates viewing/downloading the file.
_EXTRA_BACKOUT_PERMISSION_BY_KIND = {
    BulkUploadKind.CHECK_IMAGES: "paid_item:write",
}


def _get_authorized_record(db: Session, ctx: TenantContext, upload_id: uuid.UUID) -> BulkUploadFile:
    record = bulk_upload_file_service.get_uploaded_file(db, ctx.tenant_id, upload_id, ctx.customer_id)
    if record is None:
        raise WebNotFound()
    if _PERMISSION_BY_KIND[record.kind] not in ctx.permissions:
        raise WebForbidden()
    return record


def _plugin_formats(db: Session, ctx: TenantContext) -> list[tuple[BulkUploadKind, str]]:
    """Plugin import formats this bank turned on that this user may upload (the built-in ones
    have their own forms on each list's page)."""
    from openclear import plugins

    return [
        (kind, fmt) for kind, fmt in plugin_service.enabled(db, ctx.tenant_id, "import_formats")
        if not plugins.is_builtin("import_formats", (kind, fmt)) and _PERMISSION_BY_KIND.get(kind) in ctx.permissions
    ]


@router.get("/new")
def other_format_form(request: Request, db: Session = Depends(get_db), ctx: TenantContext = Depends(get_web_context)) -> HTMLResponse:
    """Upload a file in an import format a plugin provides (PLUGIN_PLAN.md PL2)."""
    return render_template(request, "bulk_uploads/other_format.html", ctx=ctx, formats=_plugin_formats(db, ctx))


@router.post("/new")
def other_format_upload(
    request: Request,
    upload_file: UploadFile,
    import_format: str = Form(...),
    create_missing_accounts: bool = Form(False),
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(get_web_context),
    _csrf: None = Depends(verify_csrf),
) -> Response:
    formats = _plugin_formats(db, ctx)
    chosen = next(((k, f) for k, f in formats if f"{k.value}/{f}" == import_format), None)
    if chosen is None:
        raise WebForbidden()
    kind, fmt = chosen
    record = bulk_import_service.receive_upload(
        db, ctx.tenant_id, kind=kind, file_format=fmt, filename=upload_file.filename or f"upload.{fmt}",
        content_type=upload_file.content_type, uploaded_by_user_id=ctx.user_id, customer_id=ctx.customer_id,
        stream=upload_file.file, options={"create_missing_accounts": create_missing_accounts},
    )
    if record.status.value == "rejected":
        return render_template(request, "bulk_uploads/other_format.html", ctx=ctx, formats=formats, status_code=422, error=record.error)
    jobs_queued(db.get_bind())
    return see_other(f"/ui/bulk-uploads/{record.id}")


@router.get("/{upload_id}")
def bulk_upload_detail(
    request: Request, upload_id: uuid.UUID, db: Session = Depends(get_db), ctx: TenantContext = Depends(get_web_context)
) -> HTMLResponse:
    record = _get_authorized_record(db, ctx, upload_id)
    # Recomputed live on every render — a stale "verified" flag stored at upload time
    # would defeat the entire point, which is detecting a change made SINCE then.
    verified = bulk_upload_file_service.verify_uploaded_file(db, ctx.tenant_id, upload_id, ctx.customer_id)
    bulk_import_service.check_job(db, record)
    finished = bulk_import_service.is_finished(record)
    tracked_records = bulk_upload_reversal_service.list_created_records(db, ctx.tenant_id, upload_id) if finished else []
    failures = bulk_import_service.row_results(db, record, failures_only=True, limit=_FAILURES_SHOWN)
    has_row_results = bool(failures) or bool(bulk_import_service.row_results(db, record, limit=1))
    back_url, back_label = _BACK_BY_KIND.get(record.kind, (None, None))
    corrections = bulk_import_service.corrections(db, record) if finished else []
    fixed_rows = {c.row_index for c in corrections if c.success}
    options = record.options or {}
    # A file in someone's own columns fails on every row (or is rejected): offer to describe
    # it with a layout, starting from this very file.
    layout_kind = {BulkUploadKind.ISSUED_ITEMS: "issued_items", BulkUploadKind.PAID_ITEMS: "paid_items"}.get(record.kind)
    offer_layout = (layout_kind is not None and options.get("format", "tabular") == "tabular"
                    and ({"issued_items": "issued_item:write", "paid_items": "paid_item:write"}[layout_kind] in ctx.permissions)
                    and (layout_kind == "issued_items" or ctx.customer_id is None)
                    and (record.status.value == "rejected" or (finished and record.failed_count and not record.succeeded_count)))
    return render_template(
        request, "bulk_uploads/detail.html", ctx=ctx, record=record, verified=verified, tracked_records=tracked_records,
        finished=finished, failures=failures, has_row_results=has_row_results, back_url=back_url, back_label=back_label,
        corrections=[c for c in corrections if c.success], fixed_rows=fixed_rows, correctable=bulk_import_service.correctable(record),
        layout_name=(options.get("layout") or {}).get("name"), offer_layout=offer_layout, layout_kind=layout_kind,
        skipped_rows=options.get("skipped_rows", 0),
        nouns=_NOUNS.get(record.kind, ("row", "rows")), record_details=_record_details(db, ctx, tracked_records),
    )


@router.get("/{upload_id}/fix")
def fix_rows_form(request: Request, upload_id: uuid.UUID, db: Session = Depends(get_db),
                  ctx: TenantContext = Depends(get_web_context)) -> Response:
    """The failed rows, each editable, with its error beside the field at fault (UX_PLAN.md U2)."""
    record = _get_authorized_record(db, ctx, upload_id)
    if not bulk_import_service.correctable(record):
        return see_other(f"/ui/bulk-uploads/{upload_id}")
    rows = bulk_import_service.open_failures(db, record)
    if not rows:
        return see_other(f"/ui/bulk-uploads/{upload_id}", flash='Every failed row is fixed.')
    return render_template(request, "bulk_uploads/fix.html", ctx=ctx, record=record, rows=rows,
                           remaining=(record.failed_count or 0) - sum(1 for c in bulk_import_service.corrections(db, record) if c.success))


@router.post("/{upload_id}/fix")
async def fix_rows(request: Request, upload_id: uuid.UUID, db: Session = Depends(get_db), ctx: TenantContext = Depends(get_web_context),
                   _csrf: None = Depends(verify_csrf)) -> Response:
    """Imports the rows ticked for import, as edited; each is recorded as a fix of this upload."""
    record = _get_authorized_record(db, ctx, upload_id)
    if not bulk_import_service.correctable(record):
        return see_other(f"/ui/bulk-uploads/{upload_id}")
    form = await request.form()
    chosen = {int(v) for v in form.getlist("fix") if str(v).isdigit()}
    edits: dict[int, dict[str, str]] = {index: {} for index in chosen}
    for key, value in form.items():
        if key.startswith("row_") and "__" in key:
            index_text, field = key.removeprefix("row_").split("__", 1)
            if index_text.isdigit() and int(index_text) in chosen:
                edits[int(index_text)][field] = str(value)
    fixed, still = bulk_import_service.apply_corrections(db, record, edits, user_id=ctx.user_id, channel="web")
    db.commit()
    message = f"{fixed} row{'s' if fixed != 1 else ''} imported." + (f" {still} still need{'s' if still == 1 else ''} fixing." if still else "")
    return see_other(f"/ui/bulk-uploads/{upload_id}/fix", flash=message)


@router.get("/{upload_id}/results.csv")
def bulk_upload_results_csv(
    upload_id: uuid.UUID, db: Session = Depends(get_db), ctx: TenantContext = Depends(get_web_context)
) -> Response:
    """Every row's outcome, for an upload imported in the background."""
    record = _get_authorized_record(db, ctx, upload_id)
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["row", "succeeded", "created_id", "error"])
    for r in bulk_import_service.row_results(db, record):
        writer.writerow([r.row_label, "yes" if r.success else "no", r.created_id or "", _csv_safe(r.error or "")])
    return Response(
        out.getvalue(), media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="upload-{upload_id}-results.csv"'},
    )


def _csv_safe(value: str) -> str:
    # An error can echo a cell from the uploaded file; don't let a spreadsheet run it.
    return "'" + value if value[:1] in ("=", "+", "-", "@", "\t", "\r") else value


@router.post("/{upload_id}/back-out")
def bulk_upload_back_out(
    request: Request,
    upload_id: uuid.UUID,
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(get_web_context),
    _csrf: None = Depends(verify_csrf),
) -> Response:
    record = _get_authorized_record(db, ctx, upload_id)
    extra_permission = _EXTRA_BACKOUT_PERMISSION_BY_KIND.get(record.kind)
    if extra_permission is not None and extra_permission not in ctx.permissions:
        raise WebForbidden()

    results = bulk_upload_reversal_service.back_out_upload(
        db, ctx.tenant_id, upload_id, actor_user_id=ctx.user_id, channel="web", scoped_customer_id=ctx.customer_id
    )
    if results is None:
        db.rollback()
        return see_other(f"/ui/bulk-uploads/{upload_id}", error='Nothing to back out — this upload has already been backed out, or nothing it created was tracked.')
    return render_template(
        request,
        "bulk_result.html",
        ctx=ctx,
        results=results,
        upload_record=record,
        back_url=f"/ui/bulk-uploads/{upload_id}",
        back_label="Back to upload details",
    )


@router.get("/{upload_id}/download")
def bulk_upload_download(
    upload_id: uuid.UUID, db: Session = Depends(get_db), ctx: TenantContext = Depends(get_web_context)
) -> Response:
    record = _get_authorized_record(db, ctx, upload_id)
    if record.quarantined_at is not None:  # malware, found after it was accepted (OW-D34)
        raise WebNotFound()
    encoded_name = quote(record.original_filename)
    # Always a generic binary download, never the Content-Type the uploader's browser
    # declared: that value is attacker-controlled, and echoing e.g. text/javascript back
    # from this origin would let a <script src> elsewhere on the site load it.
    return StreamingResponse(
        storage.iter_chunks(record.storage_path),
        media_type="application/octet-stream",
        headers={"Content-Disposition": f"attachment; filename=\"upload\"; filename*=UTF-8''{encoded_name}"},
    )
