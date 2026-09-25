# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

from fastapi import APIRouter, Depends
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from openclear.db.session import get_db
from openclear.domain.tenant import Tenant
from openclear.services.tenant_service import SERVABLE_IMAGE_CONTENT_TYPES
from openclear.web.branding_storage import read_tenant_asset
from openclear.web.deps import WebNotFound

router = APIRouter(prefix="/ui/branding", tags=["web-branding"])

_CACHE_CONTROL = "public, max-age=300"
# These are public, admin-uploaded files served from the app's own origin: tell the
# browser never to run anything in them or treat them as anything but the image type
# stated, even if one is opened directly rather than through an <img> tag.
_ASSET_HEADERS = {
    "Cache-Control": _CACHE_CONTROL,
    "Content-Security-Policy": "default-src 'none'; sandbox",
    "X-Content-Type-Options": "nosniff",
}


def _get_active_tenant(db: Session, tenant_slug: str) -> Tenant | None:
    tenant = db.execute(select(Tenant).where(Tenant.slug == tenant_slug)).scalar_one_or_none()
    return tenant if tenant is not None and tenant.is_active else None


@router.get("/{tenant_slug}/logo")
def tenant_logo(tenant_slug: str, db: Session = Depends(get_db)) -> Response:
    """Deliberately public — no auth dependency. A logo isn't sensitive (same trust level
    as a company name on a login page), and the pre-auth login page needs to show it
    before any session exists; the authenticated app shell uses this exact same route via
    ctx.tenant_slug, so there's only one branding-serving code path, not two."""
    tenant = _get_active_tenant(db, tenant_slug)
    if tenant is None or not tenant.logo_path or tenant.logo_content_type not in SERVABLE_IMAGE_CONTENT_TYPES:
        raise WebNotFound()
    data = read_tenant_asset(tenant.logo_path)
    return Response(content=data, media_type=tenant.logo_content_type, headers=_ASSET_HEADERS)


@router.get("/{tenant_slug}/favicon")
def tenant_favicon(tenant_slug: str, db: Session = Depends(get_db)) -> Response:
    tenant = _get_active_tenant(db, tenant_slug)
    if tenant is None or not tenant.favicon_path or tenant.favicon_content_type not in SERVABLE_IMAGE_CONTENT_TYPES:
        raise WebNotFound()
    data = read_tenant_asset(tenant.favicon_path)
    return Response(content=data, media_type=tenant.favicon_content_type, headers=_ASSET_HEADERS)
