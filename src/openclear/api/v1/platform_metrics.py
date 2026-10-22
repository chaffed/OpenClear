# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""`GET /api/v1/platform/metrics` (openclear/metrics.py): the service provider's monitoring
numbers in the Prometheus text format, for a platform API key with the `operations` scope."""

from fastapi import APIRouter, Depends
from fastapi.responses import PlainTextResponse
from sqlalchemy.orm import Session

from openclear.auth.platform_api_key_deps import require_platform_scope
from openclear.db.session import get_db
from openclear.domain.platform_api_key import PlatformApiKey

router = APIRouter(prefix="/platform", tags=["platform-metrics"])


@router.get("/metrics", response_class=PlainTextResponse)
def metrics(db: Session = Depends(get_db), _key: PlatformApiKey = Depends(require_platform_scope("operations"))) -> PlainTextResponse:
    """Jobs, decisions past their cut-off not yet sent, unacknowledged decision files, webhook
    deliveries, and this process's requests. Scrape it every minute or so."""
    from openclear import metrics as numbers

    return PlainTextResponse(numbers.render(db), media_type="text/plain; version=0.0.4; charset=utf-8")
