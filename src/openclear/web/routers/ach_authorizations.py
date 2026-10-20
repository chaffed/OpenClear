# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import uuid
from datetime import date
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from openclear.db.session import get_db
from openclear.db.tenancy import TenantContext
from openclear.domain.ach_authorization_rule import AchAuthorizationRule
from openclear.networks.registry import get_adapter
from openclear.repositories.ach_authorization_repo import AchAuthorizationRepository
from openclear.services import account_service, ach_authorization_service, audit_log_service, exception_service
from openclear.web.deps import render_template, require_web_permission
from openclear.web.pagination import paginate
from openclear.web.form_errors import friendly_error
from openclear.web.redirects import see_other
from openclear.web.security import verify_csrf

router = APIRouter(prefix="/ui/ach/authorizations", tags=["web-ach-authorizations"])


def _prefill_from_exception(db: Session, ctx: TenantContext, exception_id: uuid.UUID | None) -> dict | None:
    """Builds the "New ACH authorization" form's prefill dict from an ACH exception's own
    transaction -- see web/routers/exceptions.py's detail route for the identical
    get_exception + load_source_item lookup this mirrors. Returns None (a blank form, no
    error) for a missing exception, a non-ACH one, or no id at all -- a stale/bad link
    should never be a hard failure here."""
    if exception_id is None:
        return None
    item = exception_service.get_exception(db, ctx.tenant_id, exception_id, customer_id=ctx.customer_id)
    if item is None or item.network_code != "ach":
        return None
    txn = get_adapter("ach").load_source_item(db, item.source_item_id)
    if txn is None:
        return None
    return {
        "account_id": txn.account_id,
        "originator_id": txn.originator_id,
        "originator_name": txn.originator_name,
        "receiver_id": txn.receiver_id or "",
        "allowed_sec_codes": txn.sec_code,
        "effective_date": date.today().isoformat(),
    }


@router.get("")
def list_authorizations(
    request: Request, page: int = 1, db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("ach_authorization:read")),
) -> HTMLResponse:
    page_obj = paginate(
        page=page,
        count_fn=lambda: AchAuthorizationRepository(db, ctx.tenant_id, ctx.customer_id).count(),
        list_fn=lambda **kw: ach_authorization_service.list_ach_authorizations(
            db, ctx.tenant_id, customer_id=ctx.customer_id, order_by=AchAuthorizationRule.created_at.desc(), **kw
        ),
    )
    return render_template(request, "ach/authorizations_list.html", ctx=ctx, page_obj=page_obj)


@router.get("/new")
def new_authorization_form(
    request: Request,
    from_exception: uuid.UUID | None = None,
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("ach_authorization:write")),
) -> HTMLResponse:
    accounts = account_service.list_accounts(db, ctx.tenant_id, customer_id=ctx.customer_id)
    prefill = _prefill_from_exception(db, ctx, from_exception)
    return render_template(
        request, "ach/authorization_form.html", ctx=ctx, accounts=accounts, prefill=prefill, from_exception_id=from_exception
    )


@router.post("")
def create_authorization(
    request: Request,
    account_id: uuid.UUID = Form(...),
    originator_id: str = Form(...),
    originator_name: str = Form(...),
    receiver_id: str = Form(""),
    max_amount: str = Form(""),
    frequency_limit: str = Form(""),
    allowed_sec_codes: str = Form(""),
    effective_date: str = Form(...),
    expiration_date: str = Form(""),
    from_exception_id: str = Form(""),
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("ach_authorization:write")),
    _csrf: None = Depends(verify_csrf),
) -> HTMLResponse:
    try:
        prefill = _prefill_from_exception(db, ctx, uuid.UUID(from_exception_id) if from_exception_id else None)
    except ValueError:
        prefill = None

    try:
        parsed_max_amount = Decimal(max_amount) if max_amount else None
        parsed_frequency_limit = int(frequency_limit) if frequency_limit else None
        parsed_effective = date.fromisoformat(effective_date)
        parsed_expiration = date.fromisoformat(expiration_date) if expiration_date else None
        parsed_sec_codes = [c.strip().upper() for c in allowed_sec_codes.split(",") if c.strip()] or None
    except (InvalidOperation, ValueError):
        accounts = account_service.list_accounts(db, ctx.tenant_id, customer_id=ctx.customer_id)
        return render_template(
            request, "ach/authorization_form.html", ctx=ctx, accounts=accounts, prefill=prefill,
            from_exception_id=from_exception_id or None, error="Invalid input.", status_code=422,
        )

    try:
        rule = ach_authorization_service.create_ach_authorization(
            db,
            ctx.tenant_id,
            ach_authorization_service.AchAuthorizationInput(
                account_id=account_id,
                originator_id=originator_id,
                originator_name=originator_name,
                receiver_id=receiver_id or None,
                max_amount=parsed_max_amount,
                frequency_limit=parsed_frequency_limit,
                allowed_sec_codes=parsed_sec_codes,
                effective_date=parsed_effective,
                expiration_date=parsed_expiration,
            ),
            created_by_user_id=ctx.user_id,
            scoped_customer_id=ctx.customer_id,
        )
    except Exception as exc:  # noqa: BLE001 — surface e.g. an out-of-scope account to the form
        db.rollback()
        accounts = account_service.list_accounts(db, ctx.tenant_id, customer_id=ctx.customer_id)
        return render_template(
            request, "ach/authorization_form.html", ctx=ctx, accounts=accounts, prefill=prefill,
            from_exception_id=from_exception_id or None, error=friendly_error(exc, action="Could not create authorization"), status_code=422,
        )
    summary = f"Authorized ACH originator {rule.originator_name} ({rule.originator_id})"
    if from_exception_id:
        summary += f" (from exception {from_exception_id})"
    audit_log_service.record_action(
        db,
        ctx.tenant_id,
        actor_user_id=ctx.user_id,
        channel="web",
        action="ach_authorization.create",
        summary=summary,
        resource_type="ach_authorization",
        resource_id=rule.id,
    )
    db.commit()
    return see_other("/ui/ach/authorizations", flash="Authorization created.")


@router.post("/{rule_id}/revoke")
def revoke_authorization(
    rule_id: uuid.UUID,
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("ach_authorization:write")),
    _csrf: None = Depends(verify_csrf),
) -> RedirectResponse:
    rule = ach_authorization_service.revoke_ach_authorization(db, ctx.tenant_id, rule_id, scoped_customer_id=ctx.customer_id)
    if rule is not None:
        audit_log_service.record_action(
            db,
            ctx.tenant_id,
            actor_user_id=ctx.user_id,
            channel="web",
            action="ach_authorization.revoke",
            summary=f"Revoked ACH authorization for {rule.originator_name} ({rule.originator_id})",
            resource_type="ach_authorization",
            resource_id=rule.id,
        )
    db.commit()
    return see_other("/ui/ach/authorizations", flash="Authorization revoked.")
