# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import uuid

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from openclear.db.session import get_db
from openclear.db.tenancy import TenantContext
from openclear.services import audit_log_service, customer_service, wsud_service
from openclear.web.client_ip import get_client_ip
from openclear.web.deps import WebNotFound, render_template, require_web_permission
from openclear.web.redirects import see_other
from openclear.web.security import verify_csrf

router = APIRouter(tags=["web-wsud"])


def _require_customer_scope(ctx: TenantContext) -> None:
    # Deliberately unreachable from a bank-wide session regardless of permission — a
    # WSUD signed by anyone other than the actual account holder isn't a valid
    # attestation. Same outward response (404) as any other out-of-scope resource in
    # this app, not a distinguishable 403.
    if ctx.customer_id is None:
        raise WebNotFound()


# --- Customer-scoped self-service: sign a WSUD covering the customer's own returned,
# consumer-SEC-code ACH transactions. ---


@router.get("/ui/wsud")
def wsud_sign_page(
    request: Request, db: Session = Depends(get_db), ctx: TenantContext = Depends(require_web_permission("wsud:sign"))
) -> HTMLResponse:
    _require_customer_scope(ctx)
    eligible = wsud_service.list_wsud_eligible_transactions(db, ctx.tenant_id, ctx.customer_id)
    statements = wsud_service.list_wsud_statements(db, ctx.tenant_id, ctx.customer_id)
    return render_template(
        request,
        "wsud/sign.html",
        ctx=ctx,
        eligible=eligible,
        statements=statements,
        consent_disclosure_text=wsud_service.get_consent_disclosure_text(),
        attestation_text=wsud_service.get_attestation_text(),
    )


@router.post("/ui/wsud/sign")
def wsud_sign_submit(
    request: Request,
    ach_transaction_ids: list[str] = Form([]),
    signer_typed_name: str = Form(""),
    consent_to_electronic_signing: bool = Form(False),
    attest_statement_is_true: bool = Form(False),
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("wsud:sign")),
    _csrf: None = Depends(verify_csrf),
) -> HTMLResponse:
    _require_customer_scope(ctx)

    def _rerender(error: str) -> HTMLResponse:
        eligible = wsud_service.list_wsud_eligible_transactions(db, ctx.tenant_id, ctx.customer_id)
        statements = wsud_service.list_wsud_statements(db, ctx.tenant_id, ctx.customer_id)
        return render_template(
            request,
            "wsud/sign.html",
            ctx=ctx,
            eligible=eligible,
            statements=statements,
            consent_disclosure_text=wsud_service.get_consent_disclosure_text(),
            attestation_text=wsud_service.get_attestation_text(),
            error=error,
            status_code=422,
        )

    if not consent_to_electronic_signing:
        return _rerender("You must consent to sign electronically before continuing.")
    if not attest_statement_is_true:
        return _rerender("You must attest that this statement is true before continuing.")

    try:
        statement = wsud_service.sign_wsud_statement(
            db,
            ctx.tenant_id,
            ctx.customer_id,
            signed_by_user_id=ctx.user_id,
            signer_typed_name=signer_typed_name,
            ach_transaction_ids=[uuid.UUID(i) for i in ach_transaction_ids],
            signer_ip_address=get_client_ip(request),
            signer_user_agent=request.headers.get("user-agent"),
        )
    except ValueError as exc:
        db.rollback()
        return _rerender(str(exc))

    audit_log_service.record_action(
        db,
        ctx.tenant_id,
        actor_user_id=ctx.user_id,
        channel="web",
        action="wsud.sign",
        summary=f"Signed a Written Statement of Unauthorized Debit covering {len(ach_transaction_ids)} transaction(s)",
        resource_type="wsud_statement",
        resource_id=statement.id,
    )
    db.commit()
    return see_other("/ui/wsud", flash="Statement signed.")


# --- Bank-wide oversight: view a customer's signed statements. ---


@router.get("/ui/customers/{customer_id}/wsud")
def wsud_bank_view(
    request: Request,
    customer_id: uuid.UUID,
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("wsud:read")),
) -> HTMLResponse:
    customer = customer_service.get_customer(db, ctx.tenant_id, customer_id)
    if customer is None:
        raise WebNotFound()
    statements = wsud_service.list_wsud_statements(db, ctx.tenant_id, customer_id)
    rows = [
        {
            "statement": statement,
            "transactions": wsud_service.get_transactions_for_statement(db, statement),
            "valid": wsud_service.verify_wsud_signature(db, statement),
        }
        for statement in statements
    ]
    return render_template(request, "wsud/bank_view.html", ctx=ctx, customer=customer, rows=rows)
