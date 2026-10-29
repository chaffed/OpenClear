# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Custom-field values on many existing accounts or customers at once, from a file
(plans/CUSTOM_FIELDS_PLAN.md CF-D10): the same background import, signing and results as every
other bulk upload, with each row's values before and after on the upload's page."""

import csv
import io

from fastapi import APIRouter, Depends, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, Response
from sqlalchemy.orm import Session

from openclear.db.session import get_db
from openclear.db.tenancy import TenantContext
from openclear.domain.bulk_upload_file import BulkUploadKind
from openclear.services import custom_field_service
from openclear.web.bulk_import import start_import
from openclear.web.deps import WebNotFound, render_template, require_web_permission
from openclear.web.security import verify_csrf

router = APIRouter(prefix="/ui/custom-fields", tags=["web-custom-fields"])

# The path's word for each owner: what it's on, its upload kind, and its list page.
_OWNERS = {
    "accounts": ("account", BulkUploadKind.ACCOUNT_FIELDS, "/ui/accounts", "accounts"),
    "customers": ("customer", BulkUploadKind.CUSTOMER_FIELDS, "/ui/customers", "customers"),
}


def _owner(owner: str) -> tuple:
    if owner not in _OWNERS:
        raise WebNotFound()
    return _OWNERS[owner]


def _page_context(db: Session, ctx: TenantContext, owner: str) -> dict:
    field_owner, _kind, back_url, plural = _owner(owner)
    return dict(owner=owner, plural=plural, back_url=back_url, record_keys=custom_field_service.RECORD_KEYS[field_owner],
                fields=custom_field_service.list_fields(db, ctx.tenant_id, field_owner))


@router.get("/{owner}/upload")
def upload_form(request: Request, owner: str, db: Session = Depends(get_db),
                ctx: TenantContext = Depends(require_web_permission("custom_field:write"))) -> HTMLResponse:
    return render_template(request, "custom_fields/upload.html", ctx=ctx, **_page_context(db, ctx, owner))


@router.get("/{owner}/template.csv")
def upload_template(owner: str, db: Session = Depends(get_db),
                    ctx: TenantContext = Depends(require_web_permission("custom_field:write"))) -> Response:
    """A header row to fill in: the record's number and a cf_ column for each active field."""
    page = _page_context(db, ctx, owner)
    out = io.StringIO()
    csv.writer(out).writerow([page["record_keys"][0], *(f"cf_{f.key}" for f in page["fields"])])
    return Response(out.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{owner}-custom-fields.csv"'})


@router.post("/{owner}/upload")
async def upload(request: Request, owner: str, upload_file: UploadFile, blank_clears: bool = Form(False),
                 db: Session = Depends(get_db), ctx: TenantContext = Depends(require_web_permission("custom_field:write")),
                 _csrf: None = Depends(verify_csrf)) -> Response:
    _field_owner, kind, _back, _plural = _owner(owner)
    return start_import(request, db, ctx, upload_file, kind=kind, file_format="tabular", form_template="custom_fields/upload.html",
                        default_filename="fields.csv", options={"blank_clears": blank_clears},
                        extra_context=_page_context(db, ctx, owner))
