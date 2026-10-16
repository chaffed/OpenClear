# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""A customer's own API clients, for its own systems (MOBILE_PLAN.md R4;
services/api_client_service.py). The bank's API client pages, pointed at the customer's routes:
only while the bank lets the customer manage them (customer_api_client:manage), only the
clients it made, with the roles the bank offers and the customer's own accounts. A new or
rotated secret is shown once."""

import uuid
from urllib.parse import quote

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from openclear.db.session import get_db
from openclear.db.tenancy import TenantContext
from openclear.domain.account import Account
from openclear.services import api_client_service, audit_log_service, customer_people_service
from openclear.services.api_client_service import ApiClientError
from openclear.web.deps import WebNotFound, render_template, require_web_permission
from openclear.web.security import verify_csrf

BASE = "/ui/api-clients"
router = APIRouter(prefix=BASE, tags=["web-customer-api-clients"])
_manage = require_web_permission("customer_api_client:manage")


def _record(db: Session, ctx: TenantContext, action: str, summary: str, client) -> None:
    """Against the customer, so it's on the customer's activity page as well as the bank's
    action log; the api_client.* actions are security events as the bank's are."""
    audit_log_service.record_action(db, ctx.tenant_id, actor_user_id=ctx.user_id, channel="web", action=action, summary=summary,
                                     resource_type="customer", resource_id=ctx.customer_id,
                                     metadata={"api_client_id": str(client.id), "client_id": client.client_id})


def _client(db: Session, ctx: TenantContext, api_client_id: uuid.UUID):
    try:
        return api_client_service.get_customer_client(db, ctx.tenant_id, ctx.customer_id, api_client_id)
    except ApiClientError:
        raise WebNotFound() from None


def _form(request: Request, db: Session, ctx: TenantContext, *, error: str | None = None, values: dict | None = None,
          status_code: int = 200) -> HTMLResponse:
    accounts = list(db.execute(select(Account).where(Account.tenant_id == ctx.tenant_id, Account.customer_id == ctx.customer_id)
                               .order_by(Account.account_number)).scalars())
    return render_template(request, "customer_api_clients/form.html", ctx=ctx, error=error, values=values or {}, status_code=status_code,
                           groups=customer_people_service.offered_groups(db, ctx.tenant_id), accounts=accounts)


def _secret_page(request: Request, ctx: TenantContext, issued, *, rotated: bool) -> Response:
    response = render_template(request, "admin/api_client_secret.html", ctx=ctx, client=issued.client, secret=issued.secret,
                               rotated=rotated, base_path=BASE)
    response.headers["Cache-Control"] = "no-store"
    return response


@router.get("")
def list_clients(request: Request, db: Session = Depends(get_db), ctx: TenantContext = Depends(_manage)) -> HTMLResponse:
    clients = [api_client_service.describe(db, c) for c in api_client_service.list_customer_clients(db, ctx.tenant_id, ctx.customer_id)]
    return render_template(request, "admin/api_clients.html", ctx=ctx, clients=clients, base_path=BASE, for_customer=True)


@router.get("/new")
def new_client(request: Request, db: Session = Depends(get_db), ctx: TenantContext = Depends(_manage)) -> HTMLResponse:
    return _form(request, db, ctx)


@router.post("")
async def create_client(request: Request, db: Session = Depends(get_db), ctx: TenantContext = Depends(_manage),
                        _csrf: None = Depends(verify_csrf)) -> Response:
    form = await request.form()
    values = {"name": str(form.get("name") or ""), "security_group_id": str(form.get("security_group_id") or ""),
              "scope": str(form.get("scope") or "all"), "account_ids": [str(v) for v in form.getlist("account_id")],
              "read_only": form.get("read_only") == "true"}
    try:
        try:
            group_id = uuid.UUID(values["security_group_id"])
            account_ids = [uuid.UUID(a) for a in values["account_ids"]]
        except ValueError:
            raise ApiClientError("Choose a role and accounts from the lists.") from None
        issued = api_client_service.create_customer_client(
            db, ctx.tenant_id, ctx.customer_id, name=values["name"], security_group_id=group_id, read_only=values["read_only"],
            all_accounts=values["scope"] != "some", account_ids=account_ids, created_by_user_id=ctx.user_id,
        )
    except ApiClientError as exc:
        db.rollback()
        return _form(request, db, ctx, error=str(exc), values=values, status_code=422)
    _record(db, ctx, "api_client.create",
            f"Created {'read-only ' if issued.client.read_only else ''}API client {issued.client.name} ({issued.client.client_id})", issued.client)
    db.commit()
    return _secret_page(request, ctx, issued, rotated=False)


@router.post("/{api_client_id}/rotate")
def rotate_secret(request: Request, api_client_id: uuid.UUID, db: Session = Depends(get_db), ctx: TenantContext = Depends(_manage),
                  _csrf: None = Depends(verify_csrf)) -> Response:
    client = _client(db, ctx, api_client_id)
    try:
        issued = api_client_service.rotate_secret(db, ctx.tenant_id, client.id)
    except ApiClientError:
        raise WebNotFound() from None
    _record(db, ctx, "api_client.rotate_secret", f"Rotated the secret of API client {client.name}", client)
    db.commit()
    return _secret_page(request, ctx, issued, rotated=True)


@router.post("/{api_client_id}/revoke")
def revoke(api_client_id: uuid.UUID, db: Session = Depends(get_db), ctx: TenantContext = Depends(_manage),
           _csrf: None = Depends(verify_csrf)) -> RedirectResponse:
    client = api_client_service.revoke(db, ctx.tenant_id, _client(db, ctx, api_client_id).id)
    _record(db, ctx, "api_client.revoke", f"Revoked API client {client.name}", client)
    db.commit()
    return RedirectResponse(f"{BASE}?flash=API+client+revoked.", status_code=303)


@router.get("/{api_client_id}/key")
def key_pair_form(request: Request, api_client_id: uuid.UUID, db: Session = Depends(get_db),
                  ctx: TenantContext = Depends(_manage)) -> HTMLResponse:
    client = _client(db, ctx, api_client_id)
    if client.revoked_at is not None:
        raise WebNotFound()
    return render_template(request, "admin/api_client_key.html", ctx=ctx, client=client, public_key_pem=client.public_key_pem or "",
                           base_path=BASE)


@router.post("/{api_client_id}/key")
async def set_key_pair(request: Request, api_client_id: uuid.UUID, db: Session = Depends(get_db), ctx: TenantContext = Depends(_manage),
                       _csrf: None = Depends(verify_csrf)) -> Response:
    client = _client(db, ctx, api_client_id)
    public_key_pem = str((await request.form()).get("public_key_pem") or "")
    try:
        api_client_service.use_key_pair(db, ctx.tenant_id, client.id, public_key_pem)
    except ApiClientError as exc:
        db.rollback()
        return render_template(request, "admin/api_client_key.html", ctx=ctx, client=_client(db, ctx, api_client_id),
                               public_key_pem=public_key_pem, error=str(exc), status_code=422, base_path=BASE)
    _record(db, ctx, "api_client.rotate_secret", f"API client {client.name} now signs in with a key pair; its secret no longer works", client)
    db.commit()
    return RedirectResponse(f"{BASE}?flash=" + quote(f"{client.name} now signs in with its key pair."), status_code=303)


@router.post("/{api_client_id}/use-secret")
def back_to_secret(request: Request, api_client_id: uuid.UUID, db: Session = Depends(get_db), ctx: TenantContext = Depends(_manage),
                   _csrf: None = Depends(verify_csrf)) -> Response:
    client = _client(db, ctx, api_client_id)
    try:
        issued = api_client_service.use_secret(db, ctx.tenant_id, client.id)
    except ApiClientError:
        raise WebNotFound() from None
    _record(db, ctx, "api_client.rotate_secret", f"API client {client.name} signs in with a secret again; its key pair no longer works", client)
    db.commit()
    return _secret_page(request, ctx, issued, rotated=True)
