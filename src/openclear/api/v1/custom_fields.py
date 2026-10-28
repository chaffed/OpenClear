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
