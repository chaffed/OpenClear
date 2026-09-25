# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import csv
import io
import uuid
from dataclasses import asdict
from datetime import date, datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import JSONResponse, Response
from sqlalchemy.orm import Session

from openclear.auth.platform_api_key_deps import require_platform_api_key
from openclear.db.session import get_db
from openclear.domain.platform_api_key import PlatformApiKey
from openclear.domain.tenant import Tenant
from openclear.services import usage_metrics_service

router = APIRouter(prefix="/platform/usage", tags=["platform-usage"])

_CSV_COLUMNS = (
    "tenant_id", "tenant_slug", "tenant_name", "customers", "accounts", "users",
    "paid_items_check", "paid_items_ach", "paid_items_total", "exceptions", "returns",
    "sms_notifications", "bulk_uploads",
)
# Same spreadsheet-formula-injection mitigation as web/routers/users.py's CSV export --
# tenant_name/tenant_slug are free text a tenant admin controls, so they must be
# neutralized the same way before landing in a cell an external system opens in Excel.
_FORMULA_LEADING_CHARS = ("=", "+", "-", "@")


def _csv_safe(value: object) -> object:
    if isinstance(value, str) and value.startswith(_FORMULA_LEADING_CHARS):
        return f"'{value}"
    return value


def _to_csv(results: list[usage_metrics_service.TenantUsage]) -> str:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=_CSV_COLUMNS)
    writer.writeheader()
    for row in results:
        writer.writerow({key: _csv_safe(value) for key, value in asdict(row).items()})
    return buffer.getvalue()


@router.get("")
def get_usage(
    period_start: date = Query(...),
    period_end: date = Query(...),
    tenant_id: uuid.UUID | None = None,
    format: Literal["json", "csv"] = "json",
    db: Session = Depends(get_db),
    _key: PlatformApiKey = Depends(require_platform_api_key),
) -> Response:
    if period_end < period_start:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "period_end must not be before period_start")

    if tenant_id is not None:
        tenant = db.get(Tenant, tenant_id)
        if tenant is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Tenant not found")
        results = [usage_metrics_service.get_tenant_usage(db, tenant, period_start, period_end)]
    else:
        results = usage_metrics_service.get_all_tenants_usage(db, period_start, period_end)

    if format == "csv":
        return Response(
            content=_to_csv(results),
            media_type="text/csv",
            headers={"Content-Disposition": 'attachment; filename="usage.csv"'},
        )

    return JSONResponse({
        "period_start": period_start.isoformat(),
        "period_end": period_end.isoformat(),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "tenants": [{**asdict(r), "tenant_id": str(r.tenant_id)} for r in results],
    })
