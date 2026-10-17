# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Settings → Webhooks (SDK_PLAN.md P0; services/webhook_service.py): where a bank's events
are sent, the deliveries and their results, test pings and replays."""

import uuid

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.orm import Session

from openclear.db.session import get_db
from openclear.db.tenancy import TenantContext
from openclear.services import audit_log_service, webhook_service
from openclear.web.deps import WebNotFound, render_template, require_web_permission
from openclear.web.security import verify_csrf
from openclear.workers.jobs import jobs_queued

router = APIRouter(prefix="/ui/admin/webhooks", tags=["web-webhooks"])
_manage = require_web_permission("api_client:manage")


def _audit(db: Session, ctx: TenantContext, action: str, summary: str, endpoint_id) -> None:
    audit_log_service.record_action(
        db, ctx.tenant_id, actor_user_id=ctx.user_id, channel="web", action=action, summary=summary,
        resource_type="webhook_endpoint", resource_id=endpoint_id,
    )


def _form(request: Request, ctx: TenantContext, *, endpoint=None, values: dict | None = None, error: str | None = None, status_code: int = 200):
    return render_template(
        request, "admin/webhook_form.html", ctx=ctx, endpoint=endpoint, values=values or {}, error=error,
        event_types=webhook_service.EVENT_TYPES, status_code=status_code,
    )


def _secret_page(request: Request, ctx: TenantContext, endpoint, secret: str, waiting=None) -> Response:
    response = render_template(request, "admin/webhook_secret.html", ctx=ctx, endpoint=endpoint, secret=secret, waiting=waiting)
    response.headers["Cache-Control"] = "no-store"
    return response


@router.get("")
def list_webhooks(request: Request, db: Session = Depends(get_db), ctx: TenantContext = Depends(_manage)) -> HTMLResponse:
    return render_template(request, "admin/webhooks.html", ctx=ctx, endpoints=webhook_service.list_endpoints(db, ctx.tenant_id))


@router.get("/new")
def new_webhook(request: Request, ctx: TenantContext = Depends(_manage)) -> HTMLResponse:
    return _form(request, ctx, values={"event_types": list(webhook_service.EVENT_TYPES)})


@router.post("")
async def create_webhook(request: Request, db: Session = Depends(get_db), ctx: TenantContext = Depends(_manage), _csrf: None = Depends(verify_csrf)) -> Response:
    form = await request.form()
    values = {"url": form.get("url", ""), "description": form.get("description", ""), "event_types": form.getlist("event_types")}
    from openclear.services import change_request_service

    # With the bank's sensitive settings protected, it's made (its secret shown once) but sends
    # nothing until a second admin approves it (DUAL_CONTROL_PLAN.md).
    waits = change_request_service.needs_approval(db, ctx.tenant_id, "webhook.save")
    outcome = None
    try:
        endpoint, secret = webhook_service.create_endpoint(
            db, ctx.tenant_id, url=values["url"], event_types=values["event_types"], description=values["description"],
            created_by_user_id=ctx.user_id, active=not waits,
        )
        if waits:
            outcome = change_request_service.request_or_apply(
                db, "webhook.save", {"endpoint_id": str(endpoint.id), "url": endpoint.url, "event_types": endpoint.event_types,
                                     "description": endpoint.description, "is_active": True},
                tenant_id=ctx.tenant_id, user_id=ctx.user_id)
    except (webhook_service.WebhookError, ValueError) as exc:
        db.rollback()
        return _form(request, ctx, values=values, error=str(exc), status_code=422)
    _audit(db, ctx, "webhook.create", f"Added webhook {endpoint.url}" + (" (waiting for approval)" if waits else ""), endpoint.id)
    db.commit()
    return _secret_page(request, ctx, endpoint, secret, waiting=outcome.change if outcome and outcome.waiting else None)


@router.get("/{endpoint_id}")
def webhook_detail(request: Request, endpoint_id: uuid.UUID, db: Session = Depends(get_db), ctx: TenantContext = Depends(_manage)) -> HTMLResponse:
    endpoint = webhook_service.get_endpoint(db, ctx.tenant_id, endpoint_id)
    if endpoint is None:
        raise WebNotFound()
    return render_template(
        request, "admin/webhook_detail.html", ctx=ctx, endpoint=endpoint, event_types=webhook_service.EVENT_TYPES,
        deliveries=webhook_service.recent_deliveries(db, ctx.tenant_id, endpoint_id),
    )


@router.get("/{endpoint_id}/edit")
def edit_webhook(request: Request, endpoint_id: uuid.UUID, db: Session = Depends(get_db), ctx: TenantContext = Depends(_manage)) -> HTMLResponse:
    endpoint = webhook_service.get_endpoint(db, ctx.tenant_id, endpoint_id)
    if endpoint is None:
        raise WebNotFound()
    return _form(request, ctx, endpoint=endpoint, values={
        "url": endpoint.url, "description": endpoint.description or "", "event_types": endpoint.event_types, "is_active": endpoint.is_active,
    })


@router.post("/{endpoint_id}")
async def update_webhook(
    request: Request, endpoint_id: uuid.UUID, db: Session = Depends(get_db), ctx: TenantContext = Depends(_manage),
    _csrf: None = Depends(verify_csrf),
) -> Response:
    endpoint = webhook_service.get_endpoint(db, ctx.tenant_id, endpoint_id)
    if endpoint is None:
        raise WebNotFound()
    form = await request.form()
    values = {
        "url": form.get("url", ""), "description": form.get("description", ""), "event_types": form.getlist("event_types"),
        "is_active": form.get("is_active") == "true",
    }
    from urllib.parse import quote

    from openclear.services import change_request_service
    from openclear.web.routers.tenant_settings import waiting_message

    try:
        outcome = change_request_service.request_or_apply(db, "webhook.save", {"endpoint_id": str(endpoint_id), **values},
                                                          tenant_id=ctx.tenant_id, user_id=ctx.user_id)
    except (webhook_service.WebhookError, ValueError) as exc:
        db.rollback()
        return _form(request, ctx, endpoint=endpoint, values=values, error=str(exc), status_code=422)
    if outcome.waiting:
        db.commit()
        return RedirectResponse(f"/ui/admin/webhooks/{endpoint_id}?flash={quote(waiting_message(outcome))}", status_code=303)
    _audit(db, ctx, "webhook.update", f"Changed webhook {values['url']}", endpoint_id)
    db.commit()
    return RedirectResponse(f"/ui/admin/webhooks/{endpoint_id}?flash=Saved.", status_code=303)


@router.post("/{endpoint_id}/rotate")
def rotate_webhook_secret(
    request: Request, endpoint_id: uuid.UUID, db: Session = Depends(get_db), ctx: TenantContext = Depends(_manage),
    _csrf: None = Depends(verify_csrf),
) -> Response:
    try:
        endpoint, secret = webhook_service.rotate_secret(db, ctx.tenant_id, endpoint_id)
    except webhook_service.WebhookError:
        raise WebNotFound() from None
    _audit(db, ctx, "webhook.rotate_secret", f"Rotated the signing secret of webhook {endpoint.url}", endpoint_id)
    db.commit()
    return _secret_page(request, ctx, endpoint, secret)


@router.post("/{endpoint_id}/test")
def test_webhook(endpoint_id: uuid.UUID, db: Session = Depends(get_db), ctx: TenantContext = Depends(_manage), _csrf: None = Depends(verify_csrf)) -> RedirectResponse:
    try:
        webhook_service.send_test(db, ctx.tenant_id, endpoint_id)
    except webhook_service.WebhookError:
        raise WebNotFound() from None
    db.commit()
    jobs_queued(db.get_bind())
    return RedirectResponse(f"/ui/admin/webhooks/{endpoint_id}?flash=Test+event+sent.", status_code=303)


@router.post("/{endpoint_id}/deliveries/{delivery_id}/replay")
def replay_delivery(
    endpoint_id: uuid.UUID, delivery_id: uuid.UUID, db: Session = Depends(get_db), ctx: TenantContext = Depends(_manage),
    _csrf: None = Depends(verify_csrf),
) -> RedirectResponse:
    try:
        webhook_service.replay(db, ctx.tenant_id, delivery_id)
    except webhook_service.WebhookError:
        raise WebNotFound() from None
    _audit(db, ctx, "webhook.replay", "Replayed a webhook delivery", endpoint_id)
    db.commit()
    jobs_queued(db.get_bind())
    return RedirectResponse(f"/ui/admin/webhooks/{endpoint_id}?flash=Sending+again.", status_code=303)
