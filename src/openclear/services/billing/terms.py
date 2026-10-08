# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Managing the fee schedule, the bank's billing settings, and each customer's billing
terms (BILLING_PLAN.md). Every function validates its input and raises ValueError with a
message fit to show the user; callers commit and audit-log."""

import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation

from sqlalchemy import select
from sqlalchemy.orm import Session

from openclear.domain.account import Account
from openclear.domain.billing import BillingMethod, CustomerBillingTerms, CustomerFeeOverride, FeeItem
from openclear.domain.customer import Customer
from openclear.domain.tenant import Tenant
from openclear.services.billing.metrics import METRICS

_FEE_FIELDS = (
    "description", "metric", "unit_price", "tiers", "minimum_charge", "afp_code", "bank_service_code",
    "taxable", "is_active", "effective_from", "effective_to", "sort_order",
)


def _decimal(value, name: str, *, places: int) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        raise ValueError(f"{name} must be a number.") from None
    if number < 0:
        raise ValueError(f"{name} can't be negative.")
    if number.as_tuple().exponent < -places:
        raise ValueError(f"{name} can have at most {places} decimal places.")
    return number


def _validate_tiers(tiers) -> list[dict] | None:
    if not tiers:
        return None
    cleaned, previous = [], 0
    for index, tier in enumerate(tiers):
        up_to = tier.get("up_to")
        last = index == len(tiers) - 1
        if up_to is None and not last:
            raise ValueError("Only the last tier can be open-ended (no upper limit).")
        if up_to is not None:
            if int(up_to) <= previous:
                raise ValueError("Each tier's upper limit must be higher than the one before.")
            previous = int(up_to)
        if last and up_to is not None:
            raise ValueError("The last tier must be open-ended (no upper limit).")
        price = _decimal(tier.get("unit_price"), "Tier unit price", places=4)
        if price is None:
            raise ValueError("Every tier needs a unit price.")
        cleaned.append({"up_to": int(up_to) if up_to is not None else None, "unit_price": str(price)})
    return cleaned


def _apply_fee_fields(item, fields: dict, *, metrics=None) -> None:
    """Validates and sets fee fields on a FeeItem (or a PlatformFeeItem, which allows the
    platform-only metrics too)."""
    metrics = metrics if metrics is not None else METRICS
    for name, value in fields.items():
        if name not in _FEE_FIELDS:
            continue
        if name == "metric":
            if value not in metrics:
                raise ValueError(f"Unknown metric {value!r}.")
        elif name == "unit_price":
            value = _decimal(value, "Unit price", places=4) or Decimal("0")
        elif name == "minimum_charge":
            value = _decimal(value, "Minimum charge", places=2)
        elif name == "tiers":
            value = _validate_tiers(value)
        elif name == "description":
            value = (value or "").strip()
            if not value:
                raise ValueError("A description is required.")
        elif name in ("afp_code", "bank_service_code"):
            value = (value or "").strip() or None
        setattr(item, name, value)
    if item.effective_from and item.effective_to and item.effective_to < item.effective_from:
        raise ValueError("The end date can't be before the start date.")


def list_fee_items(session: Session, tenant_id: uuid.UUID) -> list[FeeItem]:
    return list(session.execute(
        select(FeeItem).where(FeeItem.tenant_id == tenant_id).order_by(FeeItem.sort_order, FeeItem.code)
    ).scalars())


def get_fee_item(session: Session, tenant_id: uuid.UUID, fee_item_id: uuid.UUID) -> FeeItem | None:
    item = session.get(FeeItem, fee_item_id)
    return item if item is not None and item.tenant_id == tenant_id else None


def create_fee_item(session: Session, tenant_id: uuid.UUID, *, code: str, **fields) -> FeeItem:
    code = (code or "").strip().upper()
    if not code:
        raise ValueError("A code is required.")
    if session.execute(select(FeeItem.id).where(FeeItem.tenant_id == tenant_id, FeeItem.code == code)).first():
        raise ValueError(f"A fee with code {code} already exists.")
    if "metric" not in fields:
        raise ValueError("A metric is required.")
    item = FeeItem(tenant_id=tenant_id, code=code, unit_price=Decimal("0"), taxable=False, is_active=True, sort_order=0)
    _apply_fee_fields(item, {"description": fields.get("description"), **fields})
    session.add(item)
    session.flush()
    return item


def update_fee_item(session: Session, tenant_id: uuid.UUID, fee_item_id: uuid.UUID, **fields) -> FeeItem:
    item = get_fee_item(session, tenant_id, fee_item_id)
    if item is None:
        raise LookupError("Fee not found.")
    _apply_fee_fields(item, fields)
    session.flush()
    return item


# Common positive pay services, offered as a starting point. Prices start at zero and no
# AFP codes are set: each bank enters its own (BILLING_PLAN.md B-D2).
STARTER_SCHEDULE = [
    ("PP-MAINT", "Positive pay monthly maintenance (per customer)", "customer_monthly"),
    ("PP-ACCT", "Check positive pay account maintenance", "account_monthly"),
    ("ACHPP-ACCT", "ACH positive pay account maintenance", "ach_account_monthly"),
    ("PP-USER", "Online user (per month)", "user_monthly"),
    ("PP-ISSUE", "Issued check loaded", "issued_items"),
    ("PP-ISSFILE", "Issued-check file received", "issue_files"),
    ("PP-ITEM", "Presented check evaluated", "presented_checks"),
    ("PP-EXCPT", "Check exception", "check_exceptions"),
    ("PP-RETURN", "Check returned", "check_returns"),
    ("PP-PAYEE", "Payee positive pay item (image processed)", "check_images"),
    ("STOP-PAY", "Stop payment placed", "stop_payments"),
    ("ACHPP-ITEM", "ACH debit evaluated", "ach_debits"),
    ("ACHPP-EXCPT", "ACH exception", "ach_exceptions"),
    ("ACHPP-RETURN", "ACH debit returned", "ach_returns"),
    ("ACHPP-RULE", "ACH authorization rule (per month)", "ach_rule_monthly"),
]


def add_starter_schedule(session: Session, tenant_id: uuid.UUID) -> list[FeeItem]:
    """Adds whichever starter fees the bank doesn't already have (by code)."""
    existing = {code for (code,) in session.execute(select(FeeItem.code).where(FeeItem.tenant_id == tenant_id))}
    added = []
    for order, (code, description, metric) in enumerate(STARTER_SCHEDULE, start=1):
        if code not in existing:
            added.append(create_fee_item(session, tenant_id, code=code, description=description, metric=metric, sort_order=order * 10))
    return added


@dataclass(frozen=True, slots=True)
class BankBillingSettings:
    customer_default_billing_method: BillingMethod
    bank_billing_method: BillingMethod
    bank_billing_account_id: uuid.UUID | None


def get_bank_settings(session: Session, tenant_id: uuid.UUID) -> BankBillingSettings:
    tenant = session.get(Tenant, tenant_id)
    return BankBillingSettings(tenant.customer_default_billing_method, tenant.bank_billing_method, tenant.bank_billing_account_id)


def update_bank_settings(
    session: Session,
    tenant_id: uuid.UUID,
    *,
    customer_default_billing_method: BillingMethod,
    bank_billing_method: BillingMethod,
    bank_billing_account_id: uuid.UUID | None,
) -> BankBillingSettings:
    if bank_billing_account_id is not None:
        account = session.get(Account, bank_billing_account_id)
        if account is None or account.tenant_id != tenant_id:
            raise ValueError("Billing account not found.")
    tenant = session.get(Tenant, tenant_id)
    tenant.customer_default_billing_method = customer_default_billing_method
    tenant.bank_billing_method = bank_billing_method
    tenant.bank_billing_account_id = bank_billing_account_id
    session.flush()
    return get_bank_settings(session, tenant_id)


def get_customer_terms(session: Session, tenant_id: uuid.UUID, customer_id: uuid.UUID) -> tuple[CustomerBillingTerms | None, list[CustomerFeeOverride]]:
    terms = session.execute(
        select(CustomerBillingTerms).where(CustomerBillingTerms.tenant_id == tenant_id, CustomerBillingTerms.customer_id == customer_id)
    ).scalar_one_or_none()
    overrides = list(session.execute(
        select(CustomerFeeOverride).where(CustomerFeeOverride.tenant_id == tenant_id, CustomerFeeOverride.customer_id == customer_id)
    ).scalars())
    return terms, overrides


def set_customer_terms(
    session: Session,
    tenant_id: uuid.UUID,
    customer_id: uuid.UUID,
    *,
    billing_method: BillingMethod,
    billing_account_id: uuid.UUID | None = None,
    discount_percent=Decimal("0"),
    waive_all: bool = False,
    notes: str | None = None,
    overrides: list[dict] | None = None,
) -> tuple[CustomerBillingTerms, list[CustomerFeeOverride]]:
    """Replaces the customer's terms and, when `overrides` is given, its full set of fee
    overrides ([{"fee_item_id": ..., "unit_price": "0.05" | None, "waived": bool}])."""
    customer = session.get(Customer, customer_id)
    if customer is None or customer.tenant_id != tenant_id:
        raise LookupError("Customer not found.")
    if billing_account_id is not None:
        account = session.get(Account, billing_account_id)
        if account is None or account.tenant_id != tenant_id or account.customer_id != customer_id:
            raise ValueError("The billing account must be one of this customer's accounts.")
    discount = _decimal(discount_percent, "Discount", places=2) or Decimal("0")
    if discount > 100:
        raise ValueError("Discount can't be more than 100%.")

    terms, existing_overrides = get_customer_terms(session, tenant_id, customer_id)
    if terms is None:
        terms = CustomerBillingTerms(tenant_id=tenant_id, customer_id=customer_id)
        session.add(terms)
    terms.billing_method = billing_method
    terms.billing_account_id = billing_account_id
    terms.discount_percent = discount
    terms.waive_all = waive_all
    terms.notes = (notes or "").strip() or None

    if overrides is not None:
        for row in existing_overrides:
            session.delete(row)
        session.flush()
        existing_overrides = []
        for entry in overrides:
            fee_item_id = uuid.UUID(str(entry["fee_item_id"]))
            if get_fee_item(session, tenant_id, fee_item_id) is None:
                raise ValueError("An override refers to a fee that doesn't exist.")
            price = _decimal(entry.get("unit_price"), "Override unit price", places=4)
            waived = bool(entry.get("waived"))
            if price is None and not waived:
                continue
            row = CustomerFeeOverride(tenant_id=tenant_id, customer_id=customer_id, fee_item_id=fee_item_id, unit_price=price, waived=waived)
            session.add(row)
            existing_overrides.append(row)
    session.flush()
    return terms, existing_overrides


def parse_period(period: str | None, start: date | None, end: date | None):
    """A billing period from either "YYYY-MM" or a from/to date pair."""
    from openclear.services.billing.metrics import Period
    from openclear.services.billing.statements import month_period

    if period:
        try:
            year, month = (int(part) for part in period.split("-"))
            return month_period(year, month)
        except (ValueError, TypeError):
            raise ValueError("Period must look like 2026-09.") from None
    if start and end:
        if end < start:
            raise ValueError("The end date can't be before the start date.")
        return Period(start, end)
    raise ValueError("Give a period (YYYY-MM) or both a start and an end date.")
