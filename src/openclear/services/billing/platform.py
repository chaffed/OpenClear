# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Billing banks for using the platform (BILLING_PLAN.md B4): the platform operator (a
CUSO, hosting partner, or anyone running OpenClear for several banks) prices each bank's
total usage with its own fee schedule. Always a hard charge: an invoice. Uses the same
metrics and pricing as bank-to-customer billing, plus a flat monthly fee per bank. The
public demo organization is never invoiced.

Invoices are calculated live; saving finalized operator invoices is a later step."""

import uuid
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from openclear.db.tenancy import acting_as_tenant
from openclear.domain.billing import BillingMethod, PlatformFeeItem
from openclear.domain.tenant import Tenant
from openclear.services.billing import terms as billing_terms
from openclear.services.billing.metrics import METRICS, Metric, Period, Volume
from openclear.services.billing.pricing import price_fee
from openclear.services.billing.statements import Statement, StatementLine


def _bank_monthly(session, tenant_id, period):
    """1 for an active bank that existed by the end of the period."""
    tenant = session.get(Tenant, tenant_id)
    if tenant is None or not tenant.is_active:
        return []
    if tenant.created_at is not None and tenant.created_at.date() > period.end:
        return []
    return [Volume(None, None, 1)]


# Every bank-to-customer metric, totalled per bank, plus the per-bank monthly fee.
PLATFORM_METRICS: dict[str, Metric] = {
    "bank_monthly": Metric("bank_monthly", "Banks on the platform (per month)", "month", "bank", _bank_monthly),
    **METRICS,
}


def list_fee_items(session: Session) -> list[PlatformFeeItem]:
    return list(session.execute(select(PlatformFeeItem).order_by(PlatformFeeItem.sort_order, PlatformFeeItem.code)).scalars())


def create_fee_item(session: Session, *, code: str, **fields) -> PlatformFeeItem:
    code = (code or "").strip().upper()
    if not code:
        raise ValueError("A code is required.")
    if session.execute(select(PlatformFeeItem.id).where(PlatformFeeItem.code == code)).first():
        raise ValueError(f"A fee with code {code} already exists.")
    if "metric" not in fields:
        raise ValueError("A metric is required.")
    item = PlatformFeeItem(code=code, unit_price=0, taxable=False, is_active=True, sort_order=0)
    billing_terms._apply_fee_fields(item, {"description": fields.get("description"), **fields}, metrics=PLATFORM_METRICS)
    session.add(item)
    session.flush()
    return item


def update_fee_item(session: Session, fee_item_id: uuid.UUID, **fields) -> PlatformFeeItem:
    item = session.get(PlatformFeeItem, fee_item_id)
    if item is None:
        raise LookupError("Fee not found.")
    billing_terms._apply_fee_fields(item, fields, metrics=PLATFORM_METRICS)
    session.flush()
    return item


def build_invoices(session: Session, period: Period, *, tenant_id: uuid.UUID | None = None) -> list[Statement]:
    """One invoice per bank with charges (or just `tenant_id`'s)."""
    fees = list(session.execute(
        select(PlatformFeeItem)
        .where(
            PlatformFeeItem.is_active.is_(True),
            or_(PlatformFeeItem.effective_from.is_(None), PlatformFeeItem.effective_from <= period.end),
            or_(PlatformFeeItem.effective_to.is_(None), PlatformFeeItem.effective_to >= period.start),
        )
        .order_by(PlatformFeeItem.sort_order, PlatformFeeItem.code)
    ).scalars())
    if not fees:
        return []
    tenants_stmt = select(Tenant).where(Tenant.is_demo.is_(False)).order_by(Tenant.name)
    if tenant_id is not None:
        tenants_stmt = tenants_stmt.where(Tenant.id == tenant_id)
    invoices = []
    for tenant in list(session.execute(tenants_stmt).scalars()):
        lines = []
        with acting_as_tenant(session, tenant.id):  # Postgres RLS: count this bank's rows
            for fee in fees:
                metric = PLATFORM_METRICS.get(fee.metric)
                if metric is None:
                    continue
                volume = sum(v.quantity for v in metric.count(session, tenant.id, period))
                if not volume:
                    continue
                priced = price_fee(
                    volume=volume, unit_price=fee.unit_price, tiers=fee.tiers, minimum_charge=fee.minimum_charge,
                    billing_method=BillingMethod.HARD_CHARGE,
                )
                lines.append(StatementLine(
                    fee_item_id=fee.id, code=fee.code, description=fee.description, metric=fee.metric, unit=metric.unit,
                    afp_code=fee.afp_code, bank_service_code=fee.bank_service_code, taxable=fee.taxable, priced=priced,
                ))
        if lines:
            invoices.append(Statement(
                period=period, customer_id=tenant.id, customer_number=tenant.slug, customer_name=tenant.name,
                billing_method=BillingMethod.HARD_CHARGE, billing_account_id=None, billing_account_number=None, lines=lines,
            ))
    return invoices
