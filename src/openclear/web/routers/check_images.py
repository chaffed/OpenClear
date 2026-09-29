# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import io
import uuid
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from PIL import Image, UnidentifiedImageError
from sqlalchemy.orm import Session

from openclear.bulk_import.x937 import X937ParseError, parse_x937_file
from openclear.bulk_import.zip_import import ZipImportError, parse_zip_manifest
from openclear.db.session import get_db
from openclear.db.tenancy import TenantContext
from openclear.domain.bulk_upload_file import BulkUploadKind
from openclear.domain.check_image import CheckImage, OcrStatus
from openclear.networks.check.bulk_import import ingest_check_image_zip_rows, ingest_x937_items
from openclear.networks.check.ocr_processing import create_check_image, queue_ocr
from openclear.ocr.storage import read_image
from openclear.repositories.check_image_repo import CheckImageRepository
from openclear.repositories.paid_item_repo import PaidItemRepository
from openclear.services import audit_log_service, bulk_upload_file_service, bulk_upload_reversal_service
from openclear.web.deps import WebForbidden, WebNotFound, get_web_context, render_template, require_web_permission
from openclear.web.pagination import paginate
from openclear.web.security import verify_csrf
from openclear.workers.jobs import jobs_queued

router = APIRouter(prefix="/ui/check-images", tags=["web-check-images"])




@router.get("")
def list_check_images(
    request: Request, page: int = 1, db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("check_image:read")),
) -> HTMLResponse:
    repo = CheckImageRepository(db, ctx.tenant_id, ctx.customer_id)
    page_obj = paginate(page=page, count_fn=repo.count, list_fn=lambda **kw: repo.list(order_by=CheckImage.created_at.desc(), **kw))
    return render_template(request, "check_images/list.html", ctx=ctx, page_obj=page_obj)


@router.get("/upload")
def upload_form(
    request: Request,
    paid_item_id: uuid.UUID | None = None,
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("check_image:write")),
) -> HTMLResponse:
    paid_items = PaidItemRepository(db, ctx.tenant_id, ctx.customer_id).list()
    return render_template(request, "check_images/upload.html", ctx=ctx, paid_items=paid_items, selected_paid_item_id=paid_item_id)


@router.post("")
def upload_check_image(
    background_tasks: BackgroundTasks,
    front_image: UploadFile = File(...),
    paid_item_id: str = "",
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("check_image:write")),
    _csrf: None = Depends(verify_csrf),
) -> RedirectResponse:
    front_bytes = front_image.file.read()
    parsed_paid_item_id = uuid.UUID(paid_item_id) if paid_item_id else None

    # The linked paid item's own customer_id is authoritative when there is one (same
    # denormalize-from-the-source-record rule as the bulk import path); an orphaned
    # image (no paid item yet) falls back to the uploader's own scope.
    customer_id = ctx.customer_id
    if parsed_paid_item_id is not None:
        paid_item = PaidItemRepository(db, ctx.tenant_id, ctx.customer_id).get(parsed_paid_item_id)
        if paid_item is None:
            raise WebNotFound()
        customer_id = paid_item.customer_id

    check_image = create_check_image(
        db, ctx.tenant_id, front_bytes=front_bytes, back_bytes=None, paid_item_id=parsed_paid_item_id, customer_id=customer_id
    )
    audit_log_service.record_action(
        db,
        ctx.tenant_id,
        actor_user_id=ctx.user_id,
        channel="web",
        action="check_image.upload",
        summary="Uploaded a check image",
        resource_type="check_image",
        resource_id=check_image.id,
    )
    queue_ocr(db, ctx.tenant_id, check_image.id)
    db.commit()
    jobs_queued(db.get_bind(), background_tasks)
    return RedirectResponse(f"/ui/check-images/{check_image.id}?flash=Uploaded.+OCR+is+processing.", status_code=303)


# NOTE: /bulk must be registered before /{check_image_id} below — FastAPI matches path
# patterns in registration order, and "bulk" would otherwise be captured as
# check_image_id and rejected as an invalid UUID before this route is ever tried.


def _require_bulk_check_image_permissions(ctx: TenantContext = Depends(get_web_context)) -> TenantContext:
    # Creates BOTH a paid_item and a check_image in one step (unlike the single-image
    # upload above, which only ever references an already-existing paid item) — requiring
    # both permissions is the safer default for a combined-creation action.
    if "paid_item:write" not in ctx.permissions or "check_image:write" not in ctx.permissions:
        raise WebForbidden()
    return ctx


@router.get("/bulk")
def bulk_upload_form(
    request: Request, ctx: TenantContext = Depends(_require_bulk_check_image_permissions)
) -> HTMLResponse:
    return render_template(request, "check_images/bulk_upload.html", ctx=ctx)


@router.post("/bulk")
async def bulk_upload_check_images(
    request: Request,
    background_tasks: BackgroundTasks,
    upload_file: UploadFile,
    format: Literal["zip", "x937"] = Form(...),
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(_require_bulk_check_image_permissions),
    _csrf: None = Depends(verify_csrf),
) -> HTMLResponse:
    content = await upload_file.read()

    # Recorded before parsing (and committed on its own) so a rejected/malformed file is
    # still captured for audit purposes, not just successful uploads — see
    # services/bulk_upload_file_service.py.
    upload_record = bulk_upload_file_service.record_uploaded_file(
        db,
        ctx.tenant_id,
        kind=BulkUploadKind.CHECK_IMAGES,
        filename=upload_file.filename or ("upload.x937" if format == "x937" else "upload.zip"),
        content_type=upload_file.content_type,
        data=content,
        uploaded_by_user_id=ctx.user_id,
        customer_id=ctx.customer_id,
    )
    db.commit()

    if format == "x937":
        try:
            items = parse_x937_file(content)
        except X937ParseError as exc:
            return render_template(
                request, "check_images/bulk_upload.html", ctx=ctx, error=str(exc), status_code=422, upload_record=upload_record
            )
        if not items:
            return render_template(
                request,
                "check_images/bulk_upload.html",
                ctx=ctx,
                error="That file has no check detail records.",
                status_code=422,
                upload_record=upload_record,
            )
        results = ingest_x937_items(db, ctx.tenant_id, items, scoped_customer_id=ctx.customer_id)
    else:
        try:
            rows, images = parse_zip_manifest(content)
        except ZipImportError as exc:
            return render_template(
                request, "check_images/bulk_upload.html", ctx=ctx, error=str(exc), status_code=422, upload_record=upload_record
            )
        if not rows:
            return render_template(
                request,
                "check_images/bulk_upload.html",
                ctx=ctx,
                error="That manifest has no data rows.",
                status_code=422,
                upload_record=upload_record,
            )
        results = ingest_check_image_zip_rows(db, ctx.tenant_id, rows, images, scoped_customer_id=ctx.customer_id)

    for r in results:
        if not r.success:
            continue
        audit_log_service.record_action(
            db,
            ctx.tenant_id,
            actor_user_id=ctx.user_id,
            channel="web",
            action="check_image.upload",
            summary=f"Check image + paid item created via bulk upload ({r.row_label})",
            resource_type="paid_item",
            resource_id=r.created_id,
        )
        bulk_upload_reversal_service.track_created_record(
            db, ctx.tenant_id, upload_record.id, resource_type="paid_item", resource_id=r.created_id, row_label=r.row_label
        )
        # r.created_id is the new paid_item's id (see networks/check/bulk_import.py); the
        # check_image OCR background task needs its own id, one lookup away via the 1:1
        # link this bulk flow always creates between the two.
        check_image = CheckImageRepository(db, ctx.tenant_id, ctx.customer_id).list(paid_item_id=r.created_id)
        if check_image:
            queue_ocr(db, ctx.tenant_id, check_image[0].id)
            bulk_upload_reversal_service.track_created_record(
                db, ctx.tenant_id, upload_record.id, resource_type="check_image", resource_id=check_image[0].id, row_label=r.row_label
            )
    bulk_upload_file_service.set_result_counts(
        db, upload_record, succeeded_count=sum(r.success for r in results), failed_count=sum(not r.success for r in results)
    )
    db.commit()
    jobs_queued(db.get_bind(), background_tasks)
    return render_template(
        request,
        "bulk_result.html",
        ctx=ctx,
        results=results,
        upload_record=upload_record,
        back_url="/ui/check-images",
        back_label="Back to check images",
    )


@router.get("/{check_image_id}")
def check_image_detail(
    request: Request,
    check_image_id: uuid.UUID,
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("check_image:read")),
) -> HTMLResponse:
    image = CheckImageRepository(db, ctx.tenant_id, ctx.customer_id).get(check_image_id)
    if image is None:
        raise WebNotFound()
    return render_template(request, "check_images/detail.html", ctx=ctx, image=image)


@router.post("/{check_image_id}/reprocess")
def reprocess_check_image(
    check_image_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("check_image:write")),
    _csrf: None = Depends(verify_csrf),
) -> RedirectResponse:
    image = CheckImageRepository(db, ctx.tenant_id, ctx.customer_id).get(check_image_id)
    if image is None:
        raise WebNotFound()
    image.ocr_status = OcrStatus.PENDING
    audit_log_service.record_action(
        db,
        ctx.tenant_id,
        actor_user_id=ctx.user_id,
        channel="web",
        action="check_image.reprocess",
        summary="Requested OCR reprocessing of a check image",
        resource_type="check_image",
        resource_id=image.id,
    )
    queue_ocr(db, ctx.tenant_id, image.id)
    db.commit()
    jobs_queued(db.get_bind(), background_tasks)
    return RedirectResponse(f"/ui/check-images/{check_image_id}?flash=Reprocessing.", status_code=303)


def _image_media_type(data: bytes) -> str:
    # Bulk-loaded images are always normalized to real PNG bytes (bulk_import/images.py),
    # but the pre-existing single-image manual upload stores whatever raw bytes it was
    # given under a ".png"-named path regardless of actual format — sniffing the real
    # format here (rather than trusting the filename) keeps this download route correct
    # for both.
    try:
        detected = Image.open(io.BytesIO(data)).format
    except UnidentifiedImageError:
        return "application/octet-stream"
    return {"PNG": "image/png", "JPEG": "image/jpeg", "TIFF": "image/tiff"}.get(detected or "", "application/octet-stream")


@router.get("/{check_image_id}/front")
def download_front_image(
    check_image_id: uuid.UUID, db: Session = Depends(get_db), ctx: TenantContext = Depends(require_web_permission("check_image:read"))
) -> Response:
    image = CheckImageRepository(db, ctx.tenant_id, ctx.customer_id).get(check_image_id)
    if image is None:
        raise WebNotFound()
    data = read_image(image.front_image_path)
    return Response(content=data, media_type=_image_media_type(data))


@router.get("/{check_image_id}/back")
def download_back_image(
    check_image_id: uuid.UUID, db: Session = Depends(get_db), ctx: TenantContext = Depends(require_web_permission("check_image:read"))
) -> Response:
    image = CheckImageRepository(db, ctx.tenant_id, ctx.customer_id).get(check_image_id)
    if image is None or image.back_image_path is None:
        raise WebNotFound()
    data = read_image(image.back_image_path)
    return Response(content=data, media_type=_image_media_type(data))
