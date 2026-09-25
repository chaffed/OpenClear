# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from openclear.domain.stop_payment import StopPayment, StopPaymentStatus
from openclear.repositories.account_repo import AccountRepository
from openclear.repositories.stop_payment_repo import StopPaymentRepository


@dataclass(frozen=True, slots=True)
class StopPaymentInput:
    account_id: uuid.UUID
    check_number: str
    amount: Decimal | None
    effective_date: date
    expiration_date: date | None
    reason: str | None


def create_stop_payment(
    session: Session,
    tenant_id: uuid.UUID,
    data: StopPaymentInput,
    *,
    created_by_user_id: uuid.UUID | None,
    scoped_customer_id: uuid.UUID | None = None,
) -> StopPayment:
    account = AccountRepository(session, tenant_id, scoped_customer_id).get(data.account_id)
    if account is None:
        raise ValueError("Account not found")

    repo = StopPaymentRepository(session, tenant_id)
    stop = StopPayment(
        account_id=data.account_id,
        customer_id=account.customer_id,
        check_number=data.check_number,
        amount=data.amount,
        effective_date=data.effective_date,
        expiration_date=data.expiration_date,
        reason=data.reason,
        created_by_user_id=created_by_user_id,
    )
    repo.add(stop)
    session.flush()
    return stop


def cancel_stop_payment(
    session: Session, tenant_id: uuid.UUID, stop_id: uuid.UUID, *, scoped_customer_id: uuid.UUID | None = None
) -> StopPayment | None:
    repo = StopPaymentRepository(session, tenant_id, scoped_customer_id)
    stop = repo.get(stop_id)
    if stop is None:
        return None
    stop.status = StopPaymentStatus.CANCELLED
    session.flush()
    return stop


def list_stop_payments(
    session: Session,
    tenant_id: uuid.UUID,
    *,
    status: StopPaymentStatus | None = None,
    customer_id: uuid.UUID | None = None,
    limit: int | None = None,
    offset: int | None = None,
    order_by: Any = None,
) -> list[StopPayment]:
    repo = StopPaymentRepository(session, tenant_id, customer_id)
    return repo.list(status=status, limit=limit, offset=offset, order_by=order_by)
