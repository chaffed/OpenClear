# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from openclear.auth.deps import require_permission
from openclear.db.session import get_db
from openclear.db.tenancy import TenantContext
from openclear.domain.tenant import Tenant
from openclear.schemas.dropbox_import import ImportRunResponse, ImportedFileResultRead
from openclear.services.dropbox_import_service import scan_and_import_tenant

router = APIRouter(prefix="/import", tags=["dropbox-import"])


@router.post("/run", response_model=ImportRunResponse)
def run_import(
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_permission("bulk_import:run")),
) -> ImportRunResponse:
    """On-demand trigger for the auto-import dropbox — see
    services/dropbox_import_service.py. Deliberately scoped to only the calling
    tenant's own subtree, and only that customer's own subdirectory if the caller is
    customer-scoped -- never a system-wide scan, same tenant-isolation guarantee every
    other JSON API endpoint already enforces. An external system that just finished
    dropping a file can call this instead of waiting for the next scheduled/cron scan."""
    tenant = db.get(Tenant, ctx.tenant_id)
    if tenant is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Tenant not found") from None

    results = scan_and_import_tenant(db, tenant, customer_id=ctx.customer_id)
    return ImportRunResponse(results=[ImportedFileResultRead.model_validate(r) for r in results])
