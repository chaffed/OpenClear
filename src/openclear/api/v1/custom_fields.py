# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""The bank's custom fields' definitions (plans/CUSTOM_FIELDS_PLAN.md CF-D8): what each key on a
customer's or account's `custom_fields` means. Values are strings everywhere in the API; a consumer
converts by the type given here."""

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from openclear.auth.deps import require_permission
from openclear.db.session import get_db
from openclear.db.tenancy import TenantContext
from openclear.services import custom_field_service

router = APIRouter(prefix="/custom-fields", tags=["custom-fields"])


class CustomFieldRead(BaseModel):
    id: uuid.UUID
    owner: str = Field(description="What the field is on: customer or account")
    key: str = Field(description="The key under custom_fields in the API, and the cf_<key> column in files")
    label: str
    field_type: str = Field(description="text, integer, decimal, date, boolean or choice. Values are strings: an ISO date, "
                                        "decimal text, true/false, a choice's option value")
    max_length: int | None = None
    options: list[str] = Field([], description="A choice field's options, those not retired")
    required: bool = Field(description="Required in people's forms only; a PUT that leaves the key out leaves the value as it is")
    unique: bool
    shown_to_customer: bool
    archived: bool = Field(description="Archived: still read, never written")
    updated_at: datetime | None = None


def _read(definition) -> CustomFieldRead:
    return CustomFieldRead(id=definition.id, owner=definition.owner, key=definition.key, label=definition.label,
                           field_type=definition.field_type, max_length=definition.max_length,
                           options=[o["value"] for o in custom_field_service.active_options(definition)], required=definition.required,
                           unique=definition.unique, shown_to_customer=definition.shown_to_customer,
                           archived=definition.archived_at is not None, updated_at=definition.updated_at)


@router.get("", response_model=list[CustomFieldRead])
def list_custom_fields(db: Session = Depends(get_db), ctx: TenantContext = Depends(require_permission("account:read"))) -> list[CustomFieldRead]:
    """Every custom field on customers and on accounts, archived ones included. A customer-scoped
    caller sees only the fields shown to the customer."""
    rows = []
    for owner in ("customer", "account"):
        for definition in custom_field_service.list_fields(db, ctx.tenant_id, owner, include_archived=True):
            if ctx.customer_id is not None and not definition.shown_to_customer:
                continue
            rows.append(_read(definition))
    return rows


# --- Setting values on many records at once (plans/CUSTOM_FIELDS_PLAN.md CF-D10) -------------------

batch_router = APIRouter(tags=["custom-fields"])

MAX_BATCH = 1000


class AccountFieldValues(BaseModel):
    account_number: str | None = Field(None, description="The account to update (or give external_account_id)")
    external_account_id: str | None = None
    custom_fields: dict[str, str | None] = Field(description="Key to value, as strings; null clears; a key left out is left as it is")


class CustomerFieldValues(BaseModel):
    customer_number: str | None = Field(None, description="The customer to update (or give external_customer_id)")
    external_customer_id: str | None = None
    custom_fields: dict[str, str | None] = Field(description="Key to value, as strings; null clears; a key left out is left as it is")


class ValuesBeforeAfter(BaseModel):
    before: dict[str, str | None]
    after: dict[str, str | None]


class FieldUpdateResult(BaseModel):
    index: int
    record: str | None = Field(None, description="The account or customer number updated")
    success: bool
    changes: ValuesBeforeAfter | None = Field(None, description="The values that changed, before and after; none if nothing did")
    error: str | None = None


class FieldUpdateResponse(BaseModel):
    upload_id: uuid.UUID = Field(description="The batch, kept as a bulk upload: GET /api/v1/bulk-uploads/{id}")
    total: int
    changed: int
    unchanged: int
    failed: int
    results: list[FieldUpdateResult]


def _update_batch(db: Session, ctx: TenantContext, kind, items: list) -> FieldUpdateResponse:
    import json

    from fastapi import HTTPException, status

    from openclear.domain.bulk_upload_file import BulkUploadSource, BulkUploadStatus
    from openclear.services import bulk_import_service

    if len(items) > MAX_BATCH:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"At most {MAX_BATCH:,} records in one call; send the rest in another.")
    rows = [{k: v for k, v in item.model_dump(mode="json").items() if v is not None} for item in items]
    record = bulk_import_service.receive_upload(
        db, ctx.tenant_id, kind=kind, file_format="json", filename=f"api-{kind.value}.json", content_type="application/json",
        uploaded_by_user_id=ctx.user_id, customer_id=ctx.customer_id, source=BulkUploadSource.API, data=json.dumps(rows).encode(),
        run_now=True,
    )
    if record.status == BulkUploadStatus.REJECTED:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, record.error)
    results = [
        FieldUpdateResult(index=r.row_index, record=r.row_label.split(": ", 1)[1] if ": " in r.row_label else None, success=r.success,
                          changes=r.changes, error=r.error)
        for r in bulk_import_service.row_results(db, record)
    ]
    changed = sum(1 for r in results if r.changes)
    succeeded = sum(1 for r in results if r.success)
    return FieldUpdateResponse(upload_id=record.id, total=len(results), changed=changed, unchanged=succeeded - changed,
                               failed=len(results) - succeeded, results=results)


@batch_router.post("/accounts/custom-fields", response_model=FieldUpdateResponse)
def update_account_fields(items: list[AccountFieldValues], db: Session = Depends(get_db),
                          ctx: TenantContext = Depends(require_permission("custom_field:write"))) -> FieldUpdateResponse:
    """Sets custom-field values on up to 1,000 existing accounts, each on its own: one that fails
    doesn't stop the rest. It never creates an account. The batch is kept as a bulk upload, with
    each record's values before and after, and audited once."""
    from openclear.domain.bulk_upload_file import BulkUploadKind

    return _update_batch(db, ctx, BulkUploadKind.ACCOUNT_FIELDS, items)


@batch_router.post("/customers/custom-fields", response_model=FieldUpdateResponse)
def update_customer_fields(items: list[CustomerFieldValues], db: Session = Depends(get_db),
                           ctx: TenantContext = Depends(require_permission("custom_field:write"))) -> FieldUpdateResponse:
    """Sets custom-field values on up to 1,000 existing customers, as the accounts' call does."""
    from openclear.domain.bulk_upload_file import BulkUploadKind

    return _update_batch(db, ctx, BulkUploadKind.CUSTOMER_FIELDS, items)
