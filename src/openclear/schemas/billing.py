# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import uuid
from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from openclear.domain.billing import BillingMethod


class MetricRead(BaseModel):
    code: str
    label: str
    unit: str
    level: str


class Tier(BaseModel):
    up_to: int | None = Field(None, description="Upper limit of this tier; null on the last tier")
    unit_price: Decimal


class FeeItemCreate(BaseModel):
    code: str = Field(..., max_length=40)
    description: str = Field(..., max_length=200)
    metric: str
    unit_price: Decimal = Decimal("0")
    tiers: list[Tier] | None = None
    minimum_charge: Decimal | None = None
    afp_code: str | None = Field(None, max_length=16)
    bank_service_code: str | None = Field(None, max_length=32)
    taxable: bool = False
    is_active: bool = True
    effective_from: date | None = None
    effective_to: date | None = None
    sort_order: int = 0


class FeeItemUpdate(BaseModel):
    description: str | None = Field(None, max_length=200)
    metric: str | None = None
    unit_price: Decimal | None = None
    tiers: list[Tier] | None = None
    minimum_charge: Decimal | None = None
    afp_code: str | None = Field(None, max_length=16)
    bank_service_code: str | None = Field(None, max_length=32)
    taxable: bool | None = None
    is_active: bool | None = None
    effective_from: date | None = None
    effective_to: date | None = None
    sort_order: int | None = None


class FeeItemRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    code: str
    description: str
    metric: str
    unit_price: Decimal
    tiers: list[dict] | None
    minimum_charge: Decimal | None
    afp_code: str | None
    bank_service_code: str | None
    taxable: bool
    is_active: bool
    effective_from: date | None
    effective_to: date | None
    sort_order: int


class BankBillingSettingsModel(BaseModel):
    customer_default_billing_method: BillingMethod
    bank_billing_method: BillingMethod
    bank_billing_account_id: uuid.UUID | None = None


class FeeOverride(BaseModel):
    fee_item_id: uuid.UUID
    unit_price: Decimal | None = None
    waived: bool = False


class CustomerTermsUpdate(BaseModel):
    billing_method: BillingMethod
    billing_account_id: uuid.UUID | None = None
    discount_percent: Decimal = Decimal("0")
    waive_all: bool = False
    notes: str | None = Field(None, max_length=500)
    overrides: list[FeeOverride] | None = Field(None, description="Replaces all of this customer's overrides when given")


class CustomerTermsRead(BaseModel):
    customer_id: uuid.UUID
    billing_method: BillingMethod
    is_default: bool = Field(description="True when the customer has no terms of its own and uses the bank's default")
    billing_account_id: uuid.UUID | None
    discount_percent: Decimal
    waive_all: bool
    notes: str | None
    overrides: list[FeeOverride]
