# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Billing (BILLING_PLAN.md): the bank's fee schedule, how its customers (and its own
accounts) are billed, and statements for any period.

`billing:read` sees statements and the report; a customer-scoped user only ever sees its
own customer's statement. `billing:manage` (bank staff only) changes the fee schedule and
billing terms."""

import uuid
from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import Response
from sqlalchemy.orm import Session

from openclear.api.v1.files import CSV_FILE, XML_FILE
from openclear.auth.deps import require_permission
from openclear.db.session import get_db
from openclear.db.tenancy import TenantContext
from openclear.schemas.billing import (
    BankBillingSettingsModel,
    CustomerTermsRead,
    CustomerTermsUpdate,
    FeeItemCreate,
    FeeItemRead,
    FeeItemUpdate,
    FeeOverride,
    MetricRead,
)
from openclear.services import audit_log_service
from openclear.services.billing import terms as billing_terms
from openclear.services.billing.metrics import METRICS
from openclear.services.billing.reports import statement_to_dict, statements_to_csv
from openclear.services.billing.exports import analysis_feed_csv, camt086_xml, postings_csv
from openclear.services.billing.finalize import finalize_period, finalized_versions, load_finalized
from openclear.services.billing.statements import build_statements

router = APIRouter(prefix="/billing", tags=["billing"])


def _audit(db: Session, ctx: TenantContext, action: str, summary: str, resource_type: str, resource_id: uuid.UUID | None) -> None:
    audit_log_service.record_action(
        db, ctx.tenant_id, actor_user_id=ctx.user_id, channel="api", action=action, summary=summary,
        resource_type=resource_type, resource_id=resource_id,
    )


@router.get("/metrics", response_model=list[MetricRead])
def list_metrics(_ctx: TenantContext = Depends(require_permission("billing:read"))) -> list[MetricRead]:
    """Everything a fee can be charged for."""
    return [MetricRead(code=m.code, label=m.label, unit=m.unit, level=m.level) for m in METRICS.values()]


# --- Fee schedule ----------------------------------------------------------------------


@router.get("/fee-items", response_model=list[FeeItemRead])
def list_fee_items(db: Session = Depends(get_db), ctx: TenantContext = Depends(require_permission("billing:manage"))) -> list[FeeItemRead]:
    return [FeeItemRead.model_validate(i) for i in billing_terms.list_fee_items(db, ctx.tenant_id)]


@router.post("/fee-items", response_model=FeeItemRead, status_code=status.HTTP_201_CREATED)
def create_fee_item(
    payload: FeeItemCreate, db: Session = Depends(get_db), ctx: TenantContext = Depends(require_permission("billing:manage"))
) -> FeeItemRead:
    data = payload.model_dump()
    data["tiers"] = [t.model_dump() for t in payload.tiers] if payload.tiers else None
    try:
        item = billing_terms.create_fee_item(db, ctx.tenant_id, **data)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from None
    _audit(db, ctx, "billing.fee_item.create", f"Added fee {item.code} ({item.description})", "billing_fee_item", item.id)
    db.commit()
    return FeeItemRead.model_validate(item)


@router.post("/fee-items/starter-schedule", response_model=list[FeeItemRead], status_code=status.HTTP_201_CREATED)
def add_starter_schedule(db: Session = Depends(get_db), ctx: TenantContext = Depends(require_permission("billing:manage"))) -> list[FeeItemRead]:
    """Adds common positive pay fees the bank doesn't have yet, priced at zero with no AFP
    codes, ready to fill in."""
    added = billing_terms.add_starter_schedule(db, ctx.tenant_id)
    if added:
        _audit(db, ctx, "billing.fee_item.starter", f"Added {len(added)} starter fees", "billing_fee_item", None)
    db.commit()
    return [FeeItemRead.model_validate(i) for i in added]


@router.patch("/fee-items/{fee_item_id}", response_model=FeeItemRead)
def update_fee_item(
    fee_item_id: uuid.UUID,
    payload: FeeItemUpdate,
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_permission("billing:manage")),
) -> FeeItemRead:
    data = payload.model_dump(exclude_unset=True)
    if "tiers" in data:
        data["tiers"] = [t.model_dump() for t in payload.tiers] if payload.tiers else None
    try:
        item = billing_terms.update_fee_item(db, ctx.tenant_id, fee_item_id, **data)
    except LookupError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Fee not found") from None
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from None
    _audit(db, ctx, "billing.fee_item.update", f"Changed fee {item.code}: {', '.join(sorted(data))}", "billing_fee_item", item.id)
    db.commit()
    return FeeItemRead.model_validate(item)


# --- Billing terms ---------------------------------------------------------------------


@router.get("/settings", response_model=BankBillingSettingsModel)
def get_settings(db: Session = Depends(get_db), ctx: TenantContext = Depends(require_permission("billing:manage"))) -> BankBillingSettingsModel:
    s = billing_terms.get_bank_settings(db, ctx.tenant_id)
    return BankBillingSettingsModel(
        customer_default_billing_method=s.customer_default_billing_method,
        bank_billing_method=s.bank_billing_method,
        bank_billing_account_id=s.bank_billing_account_id,
    )


@router.put("/settings", response_model=BankBillingSettingsModel)
def update_settings(
    payload: BankBillingSettingsModel, db: Session = Depends(get_db), ctx: TenantContext = Depends(require_permission("billing:manage"))
) -> BankBillingSettingsModel:
    try:
        billing_terms.update_bank_settings(db, ctx.tenant_id, **payload.model_dump())
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from None
    _audit(
        db, ctx, "billing.settings.update",
        f"Billing defaults: customers {payload.customer_default_billing_method.value}, bank-wide {payload.bank_billing_method.value}",
        "tenant", ctx.tenant_id,
    )
    db.commit()
    return payload


def _terms_read(db: Session, ctx: TenantContext, customer_id: uuid.UUID) -> CustomerTermsRead:
    terms, overrides = billing_terms.get_customer_terms(db, ctx.tenant_id, customer_id)
    default_method = billing_terms.get_bank_settings(db, ctx.tenant_id).customer_default_billing_method
    return CustomerTermsRead(
        customer_id=customer_id,
        billing_method=terms.billing_method if terms else default_method,
        is_default=terms is None,
        billing_account_id=terms.billing_account_id if terms else None,
        discount_percent=terms.discount_percent if terms else 0,
        waive_all=terms.waive_all if terms else False,
        notes=terms.notes if terms else None,
        overrides=[FeeOverride(fee_item_id=o.fee_item_id, unit_price=o.unit_price, waived=o.waived) for o in overrides],
    )


@router.get("/customers/{customer_id}/terms", response_model=CustomerTermsRead)
def get_customer_terms(
    customer_id: uuid.UUID, db: Session = Depends(get_db), ctx: TenantContext = Depends(require_permission("billing:manage"))
) -> CustomerTermsRead:
    from openclear.repositories.customer_repo import CustomerRepository

    if CustomerRepository(db, ctx.tenant_id).get(customer_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Customer not found")
    return _terms_read(db, ctx, customer_id)


@router.put("/customers/{customer_id}/terms", response_model=CustomerTermsRead)
def set_customer_terms(
    customer_id: uuid.UUID,
    payload: CustomerTermsUpdate,
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_permission("billing:manage")),
) -> CustomerTermsRead:
    data = payload.model_dump()
    data["overrides"] = [o.model_dump() for o in payload.overrides] if payload.overrides is not None else None
    try:
        terms, _ = billing_terms.set_customer_terms(db, ctx.tenant_id, customer_id, **data)
    except LookupError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Customer not found") from None
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from None
    _audit(
        db, ctx, "billing.customer_terms.update",
        f"Customer billed by {terms.billing_method.value}"
        + (f", {terms.discount_percent}% discount" if terms.discount_percent else "")
        + (", all charges waived" if terms.waive_all else ""),
        "customer", customer_id,
    )
    db.commit()
    return _terms_read(db, ctx, customer_id)


# --- Statements and the report ----------------------------------------------------------


def _period(period: str | None, start: date | None = None, end: date | None = None):
    try:
        return billing_terms.parse_period(period, start, end)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from None


def _statements(
    db: Session, ctx: TenantContext, period: str | None, start: date | None, end: date | None,
    customer_id: uuid.UUID | None, finalized: bool = False,
):
    billing_period = _period(period, start, end)
    if ctx.customer_id is not None:
        # A customer's own users see only their own statement.
        if finalized:
            return load_finalized(db, ctx.tenant_id, billing_period, customer_id=ctx.customer_id, only_customer=True)
        return build_statements(db, ctx.tenant_id, billing_period, customer_id=ctx.customer_id, include_bank=False)
    if finalized:
        return load_finalized(db, ctx.tenant_id, billing_period, customer_id=customer_id)
    return build_statements(db, ctx.tenant_id, billing_period, customer_id=customer_id)


@router.get("/statements")
def list_statements(
    period: str | None = None,
    start: date | None = None,
    end: date | None = None,
    customer_id: uuid.UUID | None = None,
    finalized: bool = False,
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_permission("billing:read")),
) -> list[dict]:
    """Statements for a period (`period=2026-09`, or `start` and `end` dates): calculated
    live by default, or the period's finalized version with `finalized=true`. One per
    customer with charges, plus the bank-wide one."""
    return [statement_to_dict(s) for s in _statements(db, ctx, period, start, end, customer_id, finalized)]


@router.get("/report.csv", **CSV_FILE)
def billing_report_csv(
    period: str | None = None,
    start: date | None = None,
    end: date | None = None,
    customer_id: uuid.UUID | None = None,
    finalized: bool = False,
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_permission("billing:read")),
) -> Response:
    """The billing report: one row per statement line."""
    statements = _statements(db, ctx, period, start, end, customer_id, finalized)
    label = period or f"{start}_{end}"
    return Response(
        statements_to_csv(statements),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="billing-report-{label}.csv"'},
    )


# --- Finalizing a period, and the exports ---------------------------------------------------


@router.get("/periods/{period}")
def period_status(period: str, db: Session = Depends(get_db), ctx: TenantContext = Depends(require_permission("billing:manage"))) -> dict:
    """Whether a month has been finalized, and every version it was finalized as."""
    billing_period = _period(period)
    current = load_finalized(db, ctx.tenant_id, billing_period)
    return {
        "period": period,
        "finalized": bool(current),
        "versions": finalized_versions(db, ctx.tenant_id, billing_period),
        "statements": len(current),
        "net_total": str(sum((s.net_total for s in current), Decimal("0.00"))),
    }


@router.post("/periods/{period}/finalize")
def finalize(period: str, db: Session = Depends(get_db), ctx: TenantContext = Depends(require_permission("billing:manage"))) -> dict:
    """Saves the month's statements as a frozen record (a replacement version if it was
    finalized before). The exports below are produced from it."""
    billing_period = _period(period)
    statements = finalize_period(db, ctx.tenant_id, billing_period, finalized_by_user_id=ctx.user_id)
    version = statements[0].version if statements else None
    total = sum((s.net_total for s in statements), Decimal("0.00"))
    _audit(db, ctx, "billing.period.finalize", f"Finalized billing for {period} (version {version}, {len(statements)} statements, {total} due)", "tenant", ctx.tenant_id)
    db.commit()
    return {"period": period, "version": version, "statements": len(statements), "net_total": str(total)}


def _finalized_for_export(db: Session, ctx: TenantContext, period: str):
    if ctx.customer_id is not None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Billing exports are for bank staff")
    billing_period = _period(period)
    statements = load_finalized(db, ctx.tenant_id, billing_period)
    if not statements:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Finalize {period} before exporting it")
    return statements


def _download(content, media_type: str, filename: str) -> Response:
    return Response(content, media_type=media_type, headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.get("/periods/{period}/analysis-feed.csv", **CSV_FILE)
def analysis_feed(period: str, db: Session = Depends(get_db), ctx: TenantContext = Depends(require_permission("billing:read"))) -> Response:
    """Account analysis customers' service lines, for the bank's analysis system."""
    return _download(analysis_feed_csv(_finalized_for_export(db, ctx, period)), "text/csv", f"analysis-feed-{period}.csv")


@router.get("/periods/{period}/hard-charge-postings.csv", **CSV_FILE)
def hard_charge_postings(period: str, db: Session = Depends(get_db), ctx: TenantContext = Depends(require_permission("billing:read"))) -> Response:
    """One debit per hard-charge customer with something due, for the core's fee posting."""
    return _download(postings_csv(_finalized_for_export(db, ctx, period)), "text/csv", f"hard-charge-postings-{period}.csv")


@router.get("/periods/{period}/camt086.xml", **XML_FILE)
def camt086(
    period: str,
    customer_id: uuid.UUID | None = None,
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_permission("billing:read")),
) -> Response:
    """The period's statements as an ISO 20022 camt.086 Bank Services Billing message."""
    from openclear.config import get_settings
    from openclear.domain.tenant import Tenant

    statements = _finalized_for_export(db, ctx, period)
    if customer_id is not None:
        statements = [s for s in statements if s.customer_id == customer_id]
    tenant = db.get(Tenant, ctx.tenant_id)
    xml = camt086_xml(
        statements, bank_name=tenant.name, bank_id=tenant.slug, report_id=f"{tenant.slug}-{period}-v{statements[0].version if statements else 0}",
        version=get_settings().billing_camt086_version,
    )
    return _download(xml, "application/xml", f"camt086-{period}.xml")

