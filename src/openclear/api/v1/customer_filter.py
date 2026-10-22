# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""`customer_number=` on lists that span customers (FULL_REVIEW_PLAN.md FR-D12): a bank-wide
caller narrows to one customer; a customer's own caller always sees only theirs, and naming
another is refused."""

import uuid

from fastapi import HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from openclear.db.tenancy import TenantContext
from openclear.domain.customer import Customer

CUSTOMER_NUMBER = Query(None, max_length=64, description="Only this customer's items (by the bank's customer number)")


def resolve(db: Session, ctx: TenantContext, customer_number: str | None) -> uuid.UUID | None:
    """The customer to limit to: the caller's own, or the one named, or None for every one."""
    if not customer_number:
        return ctx.customer_id
    found = db.execute(select(Customer.id).where(Customer.tenant_id == ctx.tenant_id,
                                                 Customer.customer_number == customer_number)).scalar_one_or_none()
    if found is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No customer with that number")
    if ctx.customer_id is not None and found != ctx.customer_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You can see only your own customer's items")
    return found
