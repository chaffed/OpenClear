# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Billing statements for a period (BILLING_PLAN.md): one per customer, plus one for the
bank's own accounts and bank-wide services. Calculated live from the fee schedule, each
customer's billing terms and the period's activity; nothing is saved here (finalized
snapshots are phase B2)."""

import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from openclear.domain.account import Account
from openclear.domain.billing import BillingMethod, CustomerBillingTerms, CustomerFeeOverride, FeeItem
from openclear.domain.customer import Customer
from openclear.domain.tenant import Tenant
from openclear.services.billing.metrics import METRICS, Period, Volume
from openclear.services.billing.pricing import PricedLine, price_fee


@dataclass(frozen=True, slots=True)
class StatementLine:
    fee_item_id: uuid.UUID
    code: str
    description: str
    metric: str
    unit: str  # "month" or "item"
    afp_code: str | None
    bank_service_code: str | None
    taxable: bool
    priced: PricedLine
    # Volume per account number, for account-level metrics (the analysis feed).
    accounts: dict[str, int] = field(default_factory=dict)


@dataclass(slots=True)
class Statement:
    period: Period
    customer_id: uuid.UUID | None  # None: the bank's own accounts and bank-wide services
    customer_number: str | None
    customer_name: str
    billing_method: BillingMethod
    billing_account_id: uuid.UUID | None
    billing_account_number: str | None
    lines: list[StatementLine] = field(default_factory=list)

    @property
    def list_total(self) -> Decimal:
        return sum((line.priced.list_amount for line in self.lines), Decimal("0.00"))

    @property
    def discount_total(self) -> Decimal:
        return sum((line.priced.discount_amount for line in self.lines), Decimal("0.00"))

    @property
    def waived_total(self) -> Decimal:
        return sum(
            (line.priced.list_amount - line.priced.discount_amount for line in self.lines if line.priced.payment_method == "WVED"),
            Decimal("0.00"),
        )

    @property
    def net_total(self) -> Decimal:
        return sum((line.priced.net_amount for line in self.lines), Decimal("0.00"))


def month_period(year: int, month: int) -> Period:
    start = date(year, month, 1)
    next_month = date(year + (month == 12), month % 12 + 1, 1)
    return Period(start, date.fromordinal(next_month.toordinal() - 1))


def active_fee_items(session: Session, tenant_id: uuid.UUID, period: Period) -> list[FeeItem]:
    """Fees in force at any point in the period."""
    stmt = (
        select(FeeItem)
        .where(
            FeeItem.tenant_id == tenant_id,
            FeeItem.is_active.is_(True),
            or_(FeeItem.effective_from.is_(None), FeeItem.effective_from <= period.end),
            or_(FeeItem.effective_to.is_(None), FeeItem.effective_to >= period.start),
        )
        .order_by(FeeItem.sort_order, FeeItem.code)
    )
    return list(session.execute(stmt).scalars().all())


def build_statements(
    session: Session, tenant_id: uuid.UUID, period: Period, *, customer_id: uuid.UUID | None = None, include_bank: bool = True
) -> list[Statement]:
    """Every customer's statement (or just `customer_id`'s), plus the bank-wide one when
    `include_bank` and no single customer was asked for. Statements with no lines are
    left out."""
    tenant = session.get(Tenant, tenant_id)
    fees = active_fee_items(session, tenant_id, period)
    if not fees:
        return []

    accounts = {a.id: a for a in session.execute(select(Account).where(Account.tenant_id == tenant_id)).scalars()}
    volumes: dict[str, list[Volume]] = {}
    for metric_code in {fee.metric for fee in fees}:
        metric = METRICS.get(metric_code)
        volumes[metric_code] = metric.count(session, tenant_id, period) if metric else []

    customers_stmt = select(Customer).where(Customer.tenant_id == tenant_id).order_by(Customer.customer_number)
    if customer_id is not None:
        customers_stmt = customers_stmt.where(Customer.id == customer_id)
    customers = list(session.execute(customers_stmt).scalars())
    terms = {t.customer_id: t for t in session.execute(select(CustomerBillingTerms).where(CustomerBillingTerms.tenant_id == tenant_id)).scalars()}
    overrides = {
        (o.customer_id, o.fee_item_id): o
        for o in session.execute(select(CustomerFeeOverride).where(CustomerFeeOverride.tenant_id == tenant_id)).scalars()
    }
    first_account: dict[uuid.UUID, Account] = {}
    for account in sorted(accounts.values(), key=lambda a: a.account_number):
        if account.customer_id is not None:
            first_account.setdefault(account.customer_id, account)

    def owner(volume: Volume) -> uuid.UUID | None:
        # An account's current customer is authoritative for account-level counts.
        if volume.account_id is not None and volume.account_id in accounts:
            return accounts[volume.account_id].customer_id
        return volume.customer_id

    by_owner: dict[str, dict[uuid.UUID | None, list[Volume]]] = {}
    for metric_code, rows in volumes.items():
        grouped: dict[uuid.UUID | None, list[Volume]] = defaultdict(list)
        for row in rows:
            grouped[owner(row)].append(row)
        by_owner[metric_code] = grouped

    def lines_for(owner_id: uuid.UUID | None, method: BillingMethod, customer_terms: CustomerBillingTerms | None) -> list[StatementLine]:
        lines = []
        for fee in fees:
            metric = METRICS.get(fee.metric)
            if metric is None or (metric.level == "bank" and owner_id is not None):
                continue
            rows = by_owner[fee.metric].get(owner_id, [])
            volume = sum(r.quantity for r in rows)
            if volume == 0:
                continue
            override = overrides.get((owner_id, fee.id)) if owner_id else None
            priced = price_fee(
                volume=volume,
                unit_price=fee.unit_price,
                tiers=fee.tiers,
                minimum_charge=fee.minimum_charge,
                billing_method=method,
                override_unit_price=override.unit_price if override else None,
                waived=bool((override and override.waived) or (customer_terms and customer_terms.waive_all)),
                discount_percent=customer_terms.discount_percent if customer_terms else Decimal("0"),
            )
            per_account: dict[str, int] = defaultdict(int)
            for row in rows:
                if row.account_id is not None and row.account_id in accounts:
                    per_account[accounts[row.account_id].account_number] += row.quantity
            lines.append(StatementLine(
                fee_item_id=fee.id, code=fee.code, description=fee.description, metric=fee.metric, unit=metric.unit,
                afp_code=fee.afp_code, bank_service_code=fee.bank_service_code, taxable=fee.taxable,
                priced=priced, accounts=dict(sorted(per_account.items())),
            ))
        return lines

    statements: list[Statement] = []
    for customer in customers:
        customer_terms = terms.get(customer.id)
        method = customer_terms.billing_method if customer_terms else tenant.customer_default_billing_method
        billing_account = accounts.get(customer_terms.billing_account_id) if customer_terms and customer_terms.billing_account_id else None
        billing_account = billing_account or first_account.get(customer.id)
        statement = Statement(
            period=period, customer_id=customer.id, customer_number=customer.customer_number, customer_name=customer.name,
            billing_method=method, billing_account_id=billing_account.id if billing_account else None,
            billing_account_number=billing_account.account_number if billing_account else None,
            lines=lines_for(customer.id, method, customer_terms),
        )
        if statement.lines:
            statements.append(statement)

    if include_bank and customer_id is None:
        bank_account = accounts.get(tenant.bank_billing_account_id) if tenant.bank_billing_account_id else None
        bank = Statement(
            period=period, customer_id=None, customer_number=None, customer_name=f"{tenant.name} (bank-wide)",
            billing_method=tenant.bank_billing_method, billing_account_id=bank_account.id if bank_account else None,
            billing_account_number=bank_account.account_number if bank_account else None,
            lines=lines_for(None, tenant.bank_billing_method, None),
        )
        if bank.lines:
            statements.append(bank)
    return statements
