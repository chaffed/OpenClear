# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Billing in the web UI (BILLING_PLAN.md B3): the period overview with finalize and the
exports, statements (and their PDF), the fee schedule, the bank's billing settings, and
each customer's billing terms. A customer-scoped user only ever sees its own statement."""

import uuid
from datetime import date
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from openclear.config import get_settings
from openclear.db.session import get_db
from openclear.db.tenancy import TenantContext
from openclear.domain.account import Account
from openclear.domain.billing import BillingMethod
from openclear.domain.tenant import Tenant
from openclear.repositories.customer_repo import CustomerRepository
from openclear.services import audit_log_service
from openclear.services.billing import terms as billing_terms
from openclear.services.billing.exports import analysis_feed_csv, camt086_xml, postings_csv
from openclear.services.billing.finalize import finalize_period, finalized_versions, load_finalized
from openclear.services.billing.metrics import METRICS
from openclear.services.billing.reports import statements_to_csv
from openclear.services.billing.statements import build_statements
from openclear.web.deps import WebForbidden, WebNotFound, render_template, require_web_permission
from openclear.web.security import verify_csrf
from openclear.web.redirects import see_other

router = APIRouter(prefix="/ui/billing", tags=["web-billing"])

METHOD_LABELS = {
    BillingMethod.ANALYSIS: "Account analysis",
    BillingMethod.HARD_CHARGE: "Hard charge",
    BillingMethod.EXEMPT: "Exempt",
}


def _previous_month() -> str:
    today = date.today()
    year, month = (today.year, today.month - 1) if today.month > 1 else (today.year - 1, 12)
    return f"{year}-{month:02d}"


def _shift(period: str, months: int) -> str:
    year, month = (int(p) for p in period.split("-"))
    index = year * 12 + (month - 1) + months
    return f"{index // 12}-{index % 12 + 1:02d}"


def _parse(period: str | None):
    period = period or _previous_month()
    try:
        return period, billing_terms.parse_period(period, None, None)
    except ValueError:
        raise WebNotFound() from None


def _audit(db: Session, ctx: TenantContext, action: str, summary: str, resource_type: str, resource_id) -> None:
    audit_log_service.record_action(
        db, ctx.tenant_id, actor_user_id=ctx.user_id, channel="web", action=action, summary=summary,
        resource_type=resource_type, resource_id=resource_id,
    )


def _load(db: Session, ctx: TenantContext, billing_period, view: str, customer_id=None):
    """Statements for the page: finalized when asked for (and available), else live."""
    if ctx.customer_id is not None:
        if view == "finalized":
            return load_finalized(db, ctx.tenant_id, billing_period, customer_id=ctx.customer_id, only_customer=True)
        return build_statements(db, ctx.tenant_id, billing_period, customer_id=ctx.customer_id, include_bank=False)
    if view == "finalized":
        return load_finalized(db, ctx.tenant_id, billing_period, customer_id=customer_id)
    return build_statements(db, ctx.tenant_id, billing_period, customer_id=customer_id)


# --- Overview ----------------------------------------------------------------------------


@router.get("")
def billing_overview(
    request: Request,
    period: str | None = None,
    view: str = "",
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("billing:read")),
) -> HTMLResponse:
    period, billing_period = _parse(period)
    if ctx.customer_id is not None:
        return see_other(f"/ui/billing/statement?period={period}")
    versions = finalized_versions(db, ctx.tenant_id, billing_period)
    view = view or ("finalized" if versions else "live")
    statements = _load(db, ctx, billing_period, view)
    totals = {method.value: Decimal("0.00") for method in BillingMethod}
    for s in statements:
        totals[s.billing_method.value] += s.net_total
    return render_template(
        request, "billing/overview.html", ctx=ctx, period=period, billing_period=billing_period, view=view,
        statements=statements, versions=versions, totals=totals, method_labels=METHOD_LABELS,
        previous_period=_shift(period, -1), next_period=_shift(period, 1),
        has_fees=bool(billing_terms.list_fee_items(db, ctx.tenant_id)),
    )


@router.post("/finalize")
def finalize(
    period: str = Form(...),
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("billing:manage")),
    _csrf: None = Depends(verify_csrf),
) -> RedirectResponse:
    period, billing_period = _parse(period)
    statements = finalize_period(db, ctx.tenant_id, billing_period, finalized_by_user_id=ctx.user_id)
    version = statements[0].version if statements else None
    total = sum((s.net_total for s in statements), Decimal("0.00"))
    _audit(db, ctx, "billing.period.finalize", f"Finalized billing for {period} (version {version}, {len(statements)} statements, {total} due)", "tenant", ctx.tenant_id)
    db.commit()
    return see_other(f"/ui/billing?period={period}&view=finalized&flash=Billing+for+{period}+finalized.")


def _download(content, media_type: str, filename: str) -> Response:
    return Response(content, media_type=media_type, headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.get("/report.csv")
def report_csv(
    period: str | None = None,
    view: str = "live",
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("billing:read")),
) -> Response:
    period, billing_period = _parse(period)
    return _download(statements_to_csv(_load(db, ctx, billing_period, view)), "text/csv", f"billing-report-{period}.csv")


@router.get("/export/{name}")
def export(
    name: str,
    period: str | None = None,
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("billing:read")),
) -> Response:
    if ctx.customer_id is not None:
        raise WebForbidden()
    period, billing_period = _parse(period)
    statements = load_finalized(db, ctx.tenant_id, billing_period)
    if not statements:
        return see_other(f"/ui/billing?period={period}&error=Finalize+{period}+before+exporting+it.")
    if name == "analysis-feed.csv":
        return _download(analysis_feed_csv(statements), "text/csv", f"analysis-feed-{period}.csv")
    if name == "hard-charge-postings.csv":
        return _download(postings_csv(statements), "text/csv", f"hard-charge-postings-{period}.csv")
    if name == "camt086.xml":
        tenant = db.get(Tenant, ctx.tenant_id)
        xml = camt086_xml(
            statements, bank_name=tenant.name, bank_id=tenant.slug, report_id=f"{tenant.slug}-{period}-v{statements[0].version}",
            version=get_settings().billing_camt086_version,
        )
        return _download(xml, "application/xml", f"camt086-{period}.xml")
    raise WebNotFound()


# --- A statement ---------------------------------------------------------------------------


def _one_statement(db: Session, ctx: TenantContext, period: str | None, customer: str | None, view: str):
    period, billing_period = _parse(period)
    if ctx.customer_id is not None:
        customer_id, bank_wide = ctx.customer_id, False
    elif customer == "bank":
        customer_id, bank_wide = None, True
    else:
        try:
            customer_id, bank_wide = uuid.UUID(customer or ""), False
        except ValueError:
            raise WebNotFound() from None
    if view not in ("live", "finalized"):
        view = "finalized" if load_finalized(db, ctx.tenant_id, billing_period) else "live"
    statements = _load(db, ctx, billing_period, view, None if bank_wide else customer_id)
    statement = next((s for s in statements if (s.customer_id is None) == bank_wide and (bank_wide or s.customer_id == customer_id)), None)
    return period, billing_period, view, statement


@router.get("/statement")
def statement_page(
    request: Request,
    period: str | None = None,
    customer: str | None = None,
    view: str = "",
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("billing:read")),
) -> HTMLResponse:
    period, billing_period, view, statement = _one_statement(db, ctx, period, customer, view)
    from openclear.services.doc_pdf_service import weasyprint_usable

    return render_template(
        request, "billing/statement.html", ctx=ctx, period=period, billing_period=billing_period, view=view,
        statement=statement, method_labels=METHOD_LABELS, customer_param=customer or "",
        previous_period=_shift(period, -1), next_period=_shift(period, 1), pdf_available=weasyprint_usable(),
    )


@router.get("/statement.pdf")
def statement_pdf(
    period: str | None = None,
    customer: str | None = None,
    view: str = "",
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("billing:read")),
) -> Response:
    from openclear.services.doc_pdf_service import weasyprint_usable
    from openclear.web.templates import templates

    if not weasyprint_usable():
        raise WebNotFound()
    period, billing_period, view, statement = _one_statement(db, ctx, period, customer, view)
    if statement is None:
        raise WebNotFound()
    import weasyprint

    tenant = db.get(Tenant, ctx.tenant_id)
    html = templates.env.get_template("billing/statement_pdf.html").render(
        statement=statement, tenant=tenant, method_labels=METHOD_LABELS, view=view,
    )
    pdf = weasyprint.HTML(string=html).write_pdf()
    name = statement.customer_number or "bank"
    return _download(pdf, "application/pdf", f"statement-{name}-{period}.pdf")


# --- Fee schedule ----------------------------------------------------------------------------


def _tiers_text(tiers) -> str:
    return "\n".join(f"{'*' if t['up_to'] is None else t['up_to']} {t['unit_price']}" for t in tiers or [])


def _parse_tiers(text: str) -> list[dict] | None:
    """One tier per line: "<up to> <unit price>", with "*" (or nothing) as the last
    tier's upper limit, e.g. "100 0.00", "500 0.05", "* 0.03"."""
    tiers = []
    for raw in (text or "").splitlines():
        parts = raw.replace(",", " ").split()
        if not parts:
            continue
        if len(parts) == 1:
            up_to, price = "*", parts[0]
        elif len(parts) == 2:
            up_to, price = parts
        else:
            raise ValueError(f"Couldn't read the tier line {raw.strip()!r}: use \"up to\" then \"unit price\".")
        try:
            tiers.append({"up_to": None if up_to in ("*", "-", "") else int(up_to), "unit_price": price})
        except ValueError:
            raise ValueError(f"Couldn't read the tier line {raw.strip()!r}: the upper limit must be a whole number or *.") from None
    return tiers or None


@router.get("/fees")
def fee_list(request: Request, db: Session = Depends(get_db), ctx: TenantContext = Depends(require_web_permission("billing:manage"))) -> HTMLResponse:
    return render_template(
        request, "billing/fees.html", ctx=ctx, fees=billing_terms.list_fee_items(db, ctx.tenant_id), metrics=METRICS,
    )


@router.post("/fees/starter")
def add_starter(db: Session = Depends(get_db), ctx: TenantContext = Depends(require_web_permission("billing:manage")), _csrf: None = Depends(verify_csrf)) -> RedirectResponse:
    added = billing_terms.add_starter_schedule(db, ctx.tenant_id)
    if added:
        _audit(db, ctx, "billing.fee_item.starter", f"Added {len(added)} starter fees", "billing_fee_item", None)
    db.commit()
    return see_other(f"/ui/billing/fees?flash=Added+{len(added)}+fees.+Set+their+prices+and+AFP+codes.")


def _fee_form(request, ctx, fee=None, values=None, error=None, status_code=200):
    return render_template(
        request, "billing/fee_form.html", ctx=ctx, fee=fee, values=values or {}, error=error, status_code=status_code,
        metrics=METRICS, tiers_text=_tiers_text(fee.tiers) if fee else "",
    )


@router.get("/fees/new")
def new_fee(request: Request, ctx: TenantContext = Depends(require_web_permission("billing:manage"))) -> HTMLResponse:
    return _fee_form(request, ctx)


@router.get("/fees/{fee_id}/edit")
def edit_fee(request: Request, fee_id: uuid.UUID, db: Session = Depends(get_db), ctx: TenantContext = Depends(require_web_permission("billing:manage"))) -> HTMLResponse:
    fee = billing_terms.get_fee_item(db, ctx.tenant_id, fee_id)
    if fee is None:
        raise WebNotFound()
    return _fee_form(request, ctx, fee)


def _fee_fields(form) -> dict:
    def optional_date(value):
        return date.fromisoformat(value) if value else None

    try:
        effective_from, effective_to = optional_date(form["effective_from"]), optional_date(form["effective_to"])
    except ValueError:
        raise ValueError("Dates must look like 2026-10-01.") from None
    try:
        sort_order = int(form["sort_order"] or 0)
    except ValueError:
        raise ValueError("Sort order must be a whole number.") from None
    return {
        "description": form["description"], "metric": form["metric"], "unit_price": form["unit_price"] or "0",
        "tiers": _parse_tiers(form["tiers"]), "minimum_charge": form["minimum_charge"] or None,
        "afp_code": form["afp_code"], "bank_service_code": form["bank_service_code"],
        "taxable": form["taxable"] == "on", "is_active": form["is_active"] == "on",
        "effective_from": effective_from, "effective_to": effective_to, "sort_order": sort_order,
    }


_FEE_FORM_FIELDS = ("code", "description", "metric", "unit_price", "tiers", "minimum_charge", "afp_code",
                    "bank_service_code", "taxable", "is_active", "effective_from", "effective_to", "sort_order")


@router.post("/fees")
async def save_fee(
    request: Request,
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("billing:manage")),
    _csrf: None = Depends(verify_csrf),
) -> HTMLResponse:
    raw = await request.form()
    form = {name: (raw.get(name) or "") for name in _FEE_FORM_FIELDS}
    fee_id = raw.get("fee_id") or None
    fee = billing_terms.get_fee_item(db, ctx.tenant_id, uuid.UUID(fee_id)) if fee_id else None
    if fee_id and fee is None:
        raise WebNotFound()
    try:
        fields = _fee_fields(form)
        if fee is None:
            fee = billing_terms.create_fee_item(db, ctx.tenant_id, code=form["code"], **fields)
            action, summary = "billing.fee_item.create", f"Added fee {fee.code} ({fee.description})"
        else:
            fee = billing_terms.update_fee_item(db, ctx.tenant_id, fee.id, **fields)
            action, summary = "billing.fee_item.update", f"Changed fee {fee.code}"
    except (ValueError, InvalidOperation) as exc:
        db.rollback()
        fee = billing_terms.get_fee_item(db, ctx.tenant_id, uuid.UUID(fee_id)) if fee_id else None
        return _fee_form(request, ctx, fee, values=form, error=str(exc), status_code=422)
    _audit(db, ctx, action, summary, "billing_fee_item", fee.id)
    db.commit()
    return see_other("/ui/billing/fees?flash=Fee+saved.")


# --- The bank's billing settings ----------------------------------------------------------


def _bank_accounts(db: Session, tenant_id: uuid.UUID) -> list[Account]:
    return list(db.execute(
        select(Account).where(Account.tenant_id == tenant_id, Account.customer_id.is_(None)).order_by(Account.account_number)
    ).scalars())


@router.get("/settings")
def settings_page(request: Request, db: Session = Depends(get_db), ctx: TenantContext = Depends(require_web_permission("billing:manage"))) -> HTMLResponse:
    return render_template(
        request, "billing/settings.html", ctx=ctx, settings=billing_terms.get_bank_settings(db, ctx.tenant_id),
        accounts=_bank_accounts(db, ctx.tenant_id), method_labels=METHOD_LABELS,
    )


@router.post("/settings")
def save_settings(
    request: Request,
    customer_default_billing_method: BillingMethod = Form(...),
    bank_billing_method: BillingMethod = Form(...),
    bank_billing_account_id: str = Form(""),
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("billing:manage")),
    _csrf: None = Depends(verify_csrf),
) -> HTMLResponse:
    try:
        billing_terms.update_bank_settings(
            db, ctx.tenant_id, customer_default_billing_method=customer_default_billing_method,
            bank_billing_method=bank_billing_method,
            bank_billing_account_id=uuid.UUID(bank_billing_account_id) if bank_billing_account_id else None,
        )
    except ValueError as exc:
        db.rollback()
        return render_template(
            request, "billing/settings.html", ctx=ctx, settings=billing_terms.get_bank_settings(db, ctx.tenant_id),
            accounts=_bank_accounts(db, ctx.tenant_id), method_labels=METHOD_LABELS, error=str(exc), status_code=422,
        )
    _audit(db, ctx, "billing.settings.update",
           f"Billing defaults: customers {customer_default_billing_method.value}, bank-wide {bank_billing_method.value}", "tenant", ctx.tenant_id)
    db.commit()
    return see_other("/ui/billing/settings?flash=Billing+settings+saved.")


# --- A customer's billing terms ----------------------------------------------------------------


customer_router = APIRouter(prefix="/ui/customers", tags=["web-billing"])


def _terms_page(request, db, ctx, customer, error=None, status_code=200):
    terms, overrides = billing_terms.get_customer_terms(db, ctx.tenant_id, customer.id)
    accounts = list(db.execute(
        select(Account).where(Account.tenant_id == ctx.tenant_id, Account.customer_id == customer.id).order_by(Account.account_number)
    ).scalars())
    return render_template(
        request, "billing/customer_terms.html", ctx=ctx, customer=customer, terms=terms,
        overrides={o.fee_item_id: o for o in overrides}, accounts=accounts,
        fees=[f for f in billing_terms.list_fee_items(db, ctx.tenant_id) if f.is_active],
        default_method=billing_terms.get_bank_settings(db, ctx.tenant_id).customer_default_billing_method,
        method_labels=METHOD_LABELS, error=error, status_code=status_code,
    )


@customer_router.get("/{customer_id}/billing")
def customer_terms_page(request: Request, customer_id: uuid.UUID, db: Session = Depends(get_db), ctx: TenantContext = Depends(require_web_permission("billing:manage"))) -> HTMLResponse:
    customer = CustomerRepository(db, ctx.tenant_id).get(customer_id)
    if customer is None:
        raise WebNotFound()
    return _terms_page(request, db, ctx, customer)


@customer_router.post("/{customer_id}/billing")
async def save_customer_terms(
    request: Request,
    customer_id: uuid.UUID,
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("billing:manage")),
    _csrf: None = Depends(verify_csrf),
) -> HTMLResponse:
    customer = CustomerRepository(db, ctx.tenant_id).get(customer_id)
    if customer is None:
        raise WebNotFound()
    form = await request.form()
    overrides = []
    for fee in billing_terms.list_fee_items(db, ctx.tenant_id):
        price, waived = (form.get(f"override_price_{fee.id}") or "").strip(), form.get(f"override_waived_{fee.id}") == "on"
        if price or waived:
            overrides.append({"fee_item_id": fee.id, "unit_price": price or None, "waived": waived})
    try:
        terms, _ = billing_terms.set_customer_terms(
            db, ctx.tenant_id, customer_id,
            billing_method=BillingMethod(form.get("billing_method") or "analysis"),
            billing_account_id=uuid.UUID(form["billing_account_id"]) if form.get("billing_account_id") else None,
            discount_percent=form.get("discount_percent") or "0",
            waive_all=form.get("waive_all") == "on",
            notes=form.get("notes"),
            overrides=overrides,
        )
    except (ValueError, LookupError) as exc:
        db.rollback()
        return _terms_page(request, db, ctx, customer, error=str(exc), status_code=422)
    _audit(db, ctx, "billing.customer_terms.update", f"Customer billed by {terms.billing_method.value}", "customer", customer_id)
    db.commit()
    return see_other(f"/ui/customers/{customer_id}/billing?flash=Billing+terms+saved.")
