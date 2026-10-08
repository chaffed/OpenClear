# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Prices one fee for one customer's volume (BILLING_PLAN.md).

Order of operations:
1. The unit price: a negotiated customer price replaces the list price and any tiers;
   otherwise step tiers if the fee has them, otherwise the list unit price.
2. List amount = volume x price (or the sum over tiers), rounded to cents.
3. The monthly minimum, if the service was used and the amount is below it.
4. The customer's percentage discount.
5. How it's settled: analysis (camt.086 BCMP) or hard charge (FLAT). An exempt customer, a
   waived fee, or "waive everything" keeps the calculated amount on the line but reports
   it as waived (WVED) with nothing due; a zero amount is FREE.

The charge method uses camt.086's codes: UPRC unit priced, LPRC a total of tiers (the
breakdown is kept), MCHG raised to the minimum, DPRC discounted.
"""

from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

from openclear.domain.billing import BillingMethod

_CENT = Decimal("0.01")
_RATE = Decimal("0.0001")


def _money(value: Decimal) -> Decimal:
    return value.quantize(_CENT, rounding=ROUND_HALF_UP)


@dataclass(frozen=True, slots=True)
class PricedLine:
    volume: int
    unit_price: Decimal  # the price per unit actually used (a blended rate when tiered)
    charge_method: str  # camt.086 Mtd: UPRC, LPRC, MCHG, DPRC
    list_amount: Decimal  # after tiers and minimum, before discount
    discount_amount: Decimal
    net_amount: Decimal  # what's due; zero when waived or exempt
    payment_method: str  # camt.086 PmtMtd: BCMP, FLAT, WVED, FREE
    tiers: list[dict] = field(default_factory=list)


def _tiered(volume: int, tiers: list[dict]) -> tuple[Decimal, list[dict]]:
    remaining, floor, total, breakdown = volume, 0, Decimal("0"), []
    for tier in tiers:
        up_to = tier.get("up_to")
        price = Decimal(str(tier["unit_price"]))
        quantity = remaining if up_to is None else max(0, min(remaining, int(up_to) - floor))
        if quantity:
            amount = price * quantity
            breakdown.append({"from": floor + 1, "to": floor + quantity, "quantity": quantity, "unit_price": str(price), "amount": str(_money(amount))})
            total += amount
        remaining -= quantity
        floor = int(up_to) if up_to is not None else floor + quantity
        if remaining <= 0:
            break
    return _money(total), breakdown


def price_fee(
    *,
    volume: int,
    unit_price: Decimal,
    tiers: list[dict] | None,
    minimum_charge: Decimal | None,
    billing_method: BillingMethod,
    override_unit_price: Decimal | None = None,
    waived: bool = False,
    discount_percent: Decimal = Decimal("0"),
) -> PricedLine:
    breakdown: list[dict] = []
    if override_unit_price is not None:
        rate, method = Decimal(override_unit_price), "UPRC"
        list_amount = _money(rate * volume)
    elif tiers:
        list_amount, breakdown = _tiered(volume, tiers)
        method = "LPRC"
        rate = (list_amount / volume).quantize(_RATE, rounding=ROUND_HALF_UP) if volume else Decimal(str(tiers[0]["unit_price"]))
    else:
        rate, method = Decimal(unit_price), "UPRC"
        list_amount = _money(rate * volume)

    if minimum_charge is not None and volume > 0 and list_amount < minimum_charge:
        list_amount, method = _money(Decimal(minimum_charge)), "MCHG"

    discount = _money(list_amount * Decimal(discount_percent) / 100) if discount_percent else Decimal("0.00")
    if discount and method == "UPRC":
        method = "DPRC"
    after_discount = list_amount - discount

    if waived or billing_method == BillingMethod.EXEMPT:
        payment_method, net = ("WVED" if after_discount else "FREE"), Decimal("0.00")
    elif after_discount == 0:
        payment_method, net = "FREE", Decimal("0.00")
    else:
        payment_method = "BCMP" if billing_method == BillingMethod.ANALYSIS else "FLAT"
        net = after_discount

    return PricedLine(
        volume=volume,
        unit_price=Decimal(rate).quantize(_RATE, rounding=ROUND_HALF_UP),
        charge_method=method,
        list_amount=list_amount,
        discount_amount=discount,
        net_amount=net,
        payment_method=payment_method,
        tiers=breakdown,
    )
