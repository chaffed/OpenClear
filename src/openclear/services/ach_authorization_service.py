# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from openclear.domain.ach_authorization_rule import AchAuthorizationRule, AchAuthorizationStatus
from openclear.repositories.account_repo import AccountRepository
from openclear.repositories.ach_authorization_repo import AchAuthorizationRepository


@dataclass(frozen=True, slots=True)
class AchAuthorizationInput:
    account_id: uuid.UUID
    originator_id: str
    originator_name: str
    receiver_id: str | None
    max_amount: Decimal | None
    frequency_limit: int | None
    allowed_sec_codes: list[str] | None
    effective_date: date
    expiration_date: date | None


def create_ach_authorization(
    session: Session,
    tenant_id: uuid.UUID,
    data: AchAuthorizationInput,
    *,
    created_by_user_id: uuid.UUID | None,
    scoped_customer_id: uuid.UUID | None = None,
) -> AchAuthorizationRule:
    account = AccountRepository(session, tenant_id, scoped_customer_id).get(data.account_id)
    if account is None:
        raise ValueError("Account not found")

    repo = AchAuthorizationRepository(session, tenant_id)
    rule = AchAuthorizationRule(
        account_id=data.account_id,
        customer_id=account.customer_id,
        originator_id=data.originator_id,
        originator_name=data.originator_name,
        receiver_id=data.receiver_id,
        max_amount=data.max_amount,
        frequency_limit=data.frequency_limit,
        allowed_sec_codes=data.allowed_sec_codes,
        effective_date=data.effective_date,
        expiration_date=data.expiration_date,
        created_by_user_id=created_by_user_id,
    )
    repo.add(rule)
    session.flush()
    return rule


def revoke_ach_authorization(
    session: Session, tenant_id: uuid.UUID, rule_id: uuid.UUID, *, scoped_customer_id: uuid.UUID | None = None
) -> AchAuthorizationRule | None:
    repo = AchAuthorizationRepository(session, tenant_id, scoped_customer_id)
    rule = repo.get(rule_id)
    if rule is None:
        return None
    rule.status = AchAuthorizationStatus.REVOKED
    session.flush()
    return rule


def list_ach_authorizations(
    session: Session,
    tenant_id: uuid.UUID,
    *,
    status: AchAuthorizationStatus | None = None,
    customer_id: uuid.UUID | None = None,
    limit: int | None = None,
    offset: int | None = None,
    order_by: Any = None,
) -> list[AchAuthorizationRule]:
    repo = AchAuthorizationRepository(session, tenant_id, customer_id)
    return repo.list(status=status, limit=limit, offset=offset, order_by=order_by)
