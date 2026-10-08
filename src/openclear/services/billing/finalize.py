# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Finalizing a billing period (BILLING_PLAN.md B2): the live calculation is saved as a
frozen record that later fee or terms changes can't alter, and that the analysis feed,
hard-charge postings and camt.086 export are produced from. Finalizing again saves a new
version and supersedes the old one, which is kept."""

import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from openclear.domain.billing import BillingStatementLineRecord, BillingStatementRecord
from openclear.services.billing.metrics import Period
from openclear.services.billing.pricing import PricedLine
from openclear.services.billing.statements import Statement, StatementLine, build_statements


def _current(session: Session, tenant_id: uuid.UUID, period: Period, customer_id: uuid.UUID | None = None, *, any_customer: bool = True):
    stmt = select(BillingStatementRecord).where(
        BillingStatementRecord.tenant_id == tenant_id,
        BillingStatementRecord.period_start == period.start,
        BillingStatementRecord.period_end == period.end,
        BillingStatementRecord.superseded_at.is_(None),
    )
    if not any_customer:
        stmt = stmt.where(BillingStatementRecord.customer_id == customer_id)
    return list(session.execute(stmt.order_by(BillingStatementRecord.customer_number)).scalars())


def finalize_period(session: Session, tenant_id: uuid.UUID, period: Period, *, finalized_by_user_id: uuid.UUID | None) -> list[Statement]:
    """Saves the period's statements. Returns them as finalized statements."""
    now = datetime.now(timezone.utc)
    previous = _current(session, tenant_id, period)
    last_version = max((r.version for r in previous), default=0)
    for record in previous:
        record.superseded_at = now

    records = []
    for statement in build_statements(session, tenant_id, period):
        record = BillingStatementRecord(
            tenant_id=tenant_id, customer_id=statement.customer_id, period_start=period.start, period_end=period.end,
            version=last_version + 1, customer_number=statement.customer_number, customer_name=statement.customer_name,
            billing_method=statement.billing_method, billing_account_id=statement.billing_account_id,
            billing_account_number=statement.billing_account_number, list_total=statement.list_total,
            discount_total=statement.discount_total, waived_total=statement.waived_total, net_total=statement.net_total,
            finalized_at=now, finalized_by_user_id=finalized_by_user_id,
        )
        session.add(record)
        session.flush()
        for number, line in enumerate(statement.lines, start=1):
            p = line.priced
            session.add(BillingStatementLineRecord(
                tenant_id=tenant_id, statement_id=record.id, line_number=number, fee_item_id=line.fee_item_id,
                fee_code=line.code, description=line.description, metric=line.metric, unit=line.unit,
                afp_code=line.afp_code, bank_service_code=line.bank_service_code, taxable=line.taxable,
                volume=p.volume, unit_price=p.unit_price, charge_method=p.charge_method, list_amount=p.list_amount,
                discount_amount=p.discount_amount, net_amount=p.net_amount, payment_method=p.payment_method,
                tiers=p.tiers or None, accounts=line.accounts or None,
            ))
        records.append(record)
    session.flush()
    return [_to_statement(session, r) for r in records]


def _to_statement(session: Session, record: BillingStatementRecord) -> Statement:
    lines = session.execute(
        select(BillingStatementLineRecord)
        .where(BillingStatementLineRecord.statement_id == record.id)
        .order_by(BillingStatementLineRecord.line_number)
    ).scalars()
    return Statement(
        period=Period(record.period_start, record.period_end),
        customer_id=record.customer_id, customer_number=record.customer_number, customer_name=record.customer_name,
        billing_method=record.billing_method, billing_account_id=record.billing_account_id,
        billing_account_number=record.billing_account_number,
        lines=[
            StatementLine(
                fee_item_id=line.fee_item_id, code=line.fee_code, description=line.description, metric=line.metric,
                unit=line.unit, afp_code=line.afp_code, bank_service_code=line.bank_service_code, taxable=line.taxable,
                priced=PricedLine(
                    volume=line.volume, unit_price=Decimal(line.unit_price), charge_method=line.charge_method,
                    list_amount=Decimal(line.list_amount), discount_amount=Decimal(line.discount_amount),
                    net_amount=Decimal(line.net_amount), payment_method=line.payment_method, tiers=line.tiers or [],
                ),
                accounts=line.accounts or {},
            )
            for line in lines
        ],
        record_id=record.id, version=record.version, finalized_at=record.finalized_at,
    )


def load_finalized(session: Session, tenant_id: uuid.UUID, period: Period, *, customer_id: uuid.UUID | None = None, only_customer: bool = False) -> list[Statement]:
    """The current finalized statements for a period (empty if it hasn't been finalized).
    With `only_customer`, just that customer's (a customer-scoped caller)."""
    records = _current(session, tenant_id, period, customer_id, any_customer=not only_customer and customer_id is None)
    if customer_id is not None:
        records = [r for r in records if r.customer_id == customer_id]
    return [_to_statement(session, r) for r in records]


def finalized_versions(session: Session, tenant_id: uuid.UUID, period: Period) -> list[dict]:
    """Every version a period has been finalized as, newest first."""
    rows = session.execute(
        select(BillingStatementRecord.version, BillingStatementRecord.finalized_at, BillingStatementRecord.superseded_at)
        .where(
            BillingStatementRecord.tenant_id == tenant_id,
            BillingStatementRecord.period_start == period.start,
            BillingStatementRecord.period_end == period.end,
        )
        .distinct()
        .order_by(BillingStatementRecord.version.desc())
    ).all()
    return [{"version": v, "finalized_at": f.isoformat() if f else None, "current": s is None} for v, f, s in rows]
