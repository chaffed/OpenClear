# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Per-account access: which of a customer's accounts one of the customer's people sees
(TenantMembership.all_accounts, MembershipAccount; enforced by db/account_scope.py).

A customer membership sees all of the customer's accounts until the bank limits it to some.
Bank-wide memberships always see the whole bank. Moving a membership to another customer
puts it back to all of that customer's accounts."""

import uuid
from dataclasses import dataclass

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from openclear.errors import UserFacingError
from openclear.domain.account import Account
from openclear.domain.tenant_membership import MembershipAccount, TenantMembership
from openclear.repositories.tenant_membership_repo import TenantMembershipRepository


class AccountAccessError(UserFacingError):
    """Bad input; the message is fit to show."""


@dataclass(frozen=True, slots=True)
class AccountAccess:
    membership: TenantMembership
    all_accounts: bool
    account_ids: frozenset[uuid.UUID]  # those chosen; empty when all_accounts
    customer_accounts: list[Account]  # every account of the membership's customer, to choose from


def _membership(session: Session, tenant_id: uuid.UUID, membership_id: uuid.UUID) -> TenantMembership:
    membership = TenantMembershipRepository(session, tenant_id).get(membership_id)
    if membership is None:
        raise AccountAccessError("Membership not found.")
    if membership.customer_id is None:
        raise AccountAccessError("Bank-wide staff see every account; only a customer's people can be limited to some.")
    return membership


def chosen_accounts(session: Session, membership_id: uuid.UUID) -> frozenset[uuid.UUID]:
    return frozenset(session.execute(select(MembershipAccount.account_id).where(MembershipAccount.membership_id == membership_id)).scalars())


def get_access(session: Session, tenant_id: uuid.UUID, membership_id: uuid.UUID) -> AccountAccess:
    membership = _membership(session, tenant_id, membership_id)
    accounts = list(session.execute(select(Account).where(
        Account.tenant_id == tenant_id, Account.customer_id == membership.customer_id).order_by(Account.account_number)).scalars())
    chosen = frozenset() if membership.all_accounts else chosen_accounts(session, membership.id)
    return AccountAccess(membership, membership.all_accounts, chosen, accounts)


def set_access(
    session: Session, tenant_id: uuid.UUID, membership_id: uuid.UUID, *, all_accounts: bool, account_ids: list[uuid.UUID] | None = None,
) -> AccountAccess:
    """All of the customer's accounts, or just these (at least one, all the customer's)."""
    membership = _membership(session, tenant_id, membership_id)
    wanted = set(account_ids or [])
    if not all_accounts:
        if not wanted:
            raise AccountAccessError("Choose at least one account, or allow all of the customer's accounts.")
        found = set(session.execute(select(Account.id).where(
            Account.tenant_id == tenant_id, Account.customer_id == membership.customer_id, Account.id.in_(wanted))).scalars())
        if found != wanted:
            raise AccountAccessError("Choose only this customer's accounts.")
    session.execute(delete(MembershipAccount).where(MembershipAccount.membership_id == membership.id))
    membership.all_accounts = all_accounts
    if not all_accounts:
        session.add_all(MembershipAccount(tenant_id=tenant_id, membership_id=membership.id, account_id=a) for a in sorted(wanted, key=str))
    session.flush()
    return get_access(session, tenant_id, membership.id)


def reset(session: Session, membership: TenantMembership) -> None:
    """Back to all accounts (the membership moved to another customer, or to bank-wide)."""
    session.execute(delete(MembershipAccount).where(MembershipAccount.membership_id == membership.id))
    membership.all_accounts = True


def describe(access: AccountAccess) -> str:
    if access.all_accounts:
        return "All of the customer's accounts"
    numbers = [a.account_number for a in access.customer_accounts if a.id in access.account_ids]
    return f"{len(numbers)} account{'s' if len(numbers) != 1 else ''}: {', '.join(numbers[:10])}{'…' if len(numbers) > 10 else ''}"
