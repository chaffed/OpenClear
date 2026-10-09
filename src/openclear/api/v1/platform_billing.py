# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Billing banks for the platform (BILLING_PLAN.md B4), for the platform operator: a
platform API key with the `billing` scope. The operator's own fee schedule, and each
bank's invoice for a period (JSON, CSV, or camt.086)."""

import uuid
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import Response
from sqlalchemy.orm import Session

from openclear.api.v1.files import CSV_FILE, XML_FILE
from openclear.auth.platform_api_key_deps import require_platform_scope
from openclear.config import get_settings
from openclear.db.session import get_db
from openclear.domain.platform_api_key import PlatformApiKey
from openclear.schemas.billing import FeeItemCreate, FeeItemRead, FeeItemUpdate, MetricRead
from openclear.services.billing import platform as platform_billing
from openclear.services.billing import terms as billing_terms
from openclear.services.billing.exports import camt086_xml
from openclear.services.billing.reports import statement_to_dict, statements_to_csv

router = APIRouter(prefix="/platform/billing", tags=["platform-billing"])
_operator = require_platform_scope("billing")


def _invoice_dict(invoice) -> dict:
    data = statement_to_dict(invoice)
    return {
        "bank_id": data.pop("customer_id"),
        "bank_slug": data.pop("customer_number"),
        "bank_name": data.pop("customer_name"),
        **{k: v for k, v in data.items() if k not in ("billing_method", "billing_account_number")},
    }


@router.get("/metrics", response_model=list[MetricRead])
def metrics(_key: PlatformApiKey = Depends(_operator)) -> list[MetricRead]:
    return [MetricRead(code=m.code, label=m.label, unit=m.unit, level=m.level) for m in platform_billing.PLATFORM_METRICS.values()]


@router.get("/fee-items", response_model=list[FeeItemRead])
def list_fee_items(db: Session = Depends(get_db), _key: PlatformApiKey = Depends(_operator)) -> list[FeeItemRead]:
    return [FeeItemRead.model_validate(i) for i in platform_billing.list_fee_items(db)]


@router.post("/fee-items", response_model=FeeItemRead, status_code=status.HTTP_201_CREATED)
def create_fee_item(payload: FeeItemCreate, db: Session = Depends(get_db), _key: PlatformApiKey = Depends(_operator)) -> FeeItemRead:
    data = payload.model_dump()
    data["tiers"] = [t.model_dump() for t in payload.tiers] if payload.tiers else None
    try:
        item = platform_billing.create_fee_item(db, **data)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from None
    db.commit()
    return FeeItemRead.model_validate(item)


@router.patch("/fee-items/{fee_item_id}", response_model=FeeItemRead)
def update_fee_item(fee_item_id: uuid.UUID, payload: FeeItemUpdate, db: Session = Depends(get_db), _key: PlatformApiKey = Depends(_operator)) -> FeeItemRead:
    data = payload.model_dump(exclude_unset=True)
    if "tiers" in data:
        data["tiers"] = [t.model_dump() for t in payload.tiers] if payload.tiers else None
    try:
        item = platform_billing.update_fee_item(db, fee_item_id, **data)
    except LookupError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Fee not found") from None
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from None
    db.commit()
    return FeeItemRead.model_validate(item)


def _invoices(db: Session, period: str | None, start: date | None, end: date | None, tenant_id: uuid.UUID | None):
    try:
        billing_period = billing_terms.parse_period(period, start, end)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from None
    return platform_billing.build_invoices(db, billing_period, tenant_id=tenant_id)


@router.get("/invoices")
def invoices(
    period: str | None = None, start: date | None = None, end: date | None = None, tenant_id: uuid.UUID | None = None,
    db: Session = Depends(get_db), _key: PlatformApiKey = Depends(_operator),
) -> list[dict]:
    """Each bank's invoice for a period (`period=2026-09`, or `start` and `end`)."""
    return [_invoice_dict(i) for i in _invoices(db, period, start, end, tenant_id)]


@router.get("/invoices.csv", **CSV_FILE)
def invoices_csv(
    period: str | None = None, start: date | None = None, end: date | None = None,
    db: Session = Depends(get_db), _key: PlatformApiKey = Depends(_operator),
) -> Response:
    label = period or f"{start}_{end}"
    return Response(
        statements_to_csv(_invoices(db, period, start, end, None)), media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="platform-invoices-{label}.csv"'},
    )


@router.get("/invoices/camt086.xml", **XML_FILE)
def invoices_camt086(period: str, tenant_id: uuid.UUID | None = None, db: Session = Depends(get_db), _key: PlatformApiKey = Depends(_operator)) -> Response:
    settings = get_settings()
    xml = camt086_xml(
        _invoices(db, period, None, None, tenant_id), bank_name=settings.platform_operator_name, bank_id=settings.platform_operator_id,
        report_id=f"platform-{period}", version=settings.billing_camt086_version,
    )
    return Response(xml, media_type="application/xml", headers={"Content-Disposition": f'attachment; filename="platform-camt086-{period}.xml"'})
