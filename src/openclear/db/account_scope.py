# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Per-account access: a customer's person who may see only some of the customer's accounts
(TenantMembership.all_accounts off; services/account_access_service.py).

Like the tenant binding for Postgres row-level security (db/tenancy.py), it's set once on the
request's session, from the membership, by auth/deps.py::decode_and_build_context. From then
on every ORM query on that session, the hand-written ones included, sees only those accounts
and what's on them: accounts, issued items, stop payments, paid items, ACH rules and
transactions, the exceptions raised on paid items and ACH transactions, their decisions and
check images. Bulk uploads and data exports, which can span accounts, are theirs alone. A row
outside the scope isn't found, so it can't be read, changed or used as the parent of a new one.

Background work done for such a person (their bulk upload's import, their data export) binds
the same scope with bind_for_member.

Customer scoping (customer_id) still applies as before; this narrows it further."""

import uuid
from collections.abc import Iterable

from sqlalchemy import event, or_, select
from sqlalchemy.orm import ORMExecuteState, Session, with_loader_criteria

_KEY = "openclear_account_ids"
_USER_KEY = "openclear_account_scope_user"


def bind_session_to_accounts(session: Session, account_ids: Iterable[uuid.UUID] | None, *, user_id: uuid.UUID | None = None) -> None:
    """Limit the session to these accounts (and to `user_id`'s own uploads and exports), or
    lift the limit (None)."""
    if account_ids is None:
        session.info.pop(_KEY, None)
        session.info.pop(_USER_KEY, None)
    else:
        session.info[_KEY] = tuple(sorted(set(account_ids), key=str))
        session.info[_USER_KEY] = user_id


def membership_accounts(session: Session, membership) -> frozenset[uuid.UUID] | None:
    """The accounts a membership is limited to, or None for all it can otherwise see."""
    if membership.customer_id is None or membership.all_accounts:
        return None
    from openclear.domain.tenant_membership import MembershipAccount

    return frozenset(session.execute(
        select(MembershipAccount.account_id).where(MembershipAccount.membership_id == membership.id)).scalars())


def bind_for_member(session: Session, tenant_id: uuid.UUID, user_id: uuid.UUID | None, customer_id: uuid.UUID | None) -> None:
    """For background work on someone's behalf: the scope their membership gives them."""
    from openclear.domain.tenant_membership import TenantMembership

    membership = None
    if user_id is not None and customer_id is not None:
        membership = session.execute(select(TenantMembership).where(
            TenantMembership.tenant_id == tenant_id, TenantMembership.user_id == user_id,
            TenantMembership.customer_id == customer_id)).scalar_one_or_none()
    bind_session_to_accounts(session, membership_accounts(session, membership) if membership else None, user_id=user_id)


def account_ids(session: Session) -> tuple[uuid.UUID, ...] | None:
    """The accounts this session is limited to, or None when it isn't."""
    return session.info.get(_KEY)


def _criteria(ids: tuple[uuid.UUID, ...], user_id: uuid.UUID | None) -> list:
    from openclear.domain.account import Account
    from openclear.domain.ach_authorization_rule import AchAuthorizationRule
    from openclear.domain.ach_transaction import AchTransaction
    from openclear.domain.bulk_upload_file import BulkUploadFile
    from openclear.domain.check_image import CheckImage
    from openclear.domain.data_export_job import DataExportJob
    from openclear.domain.decision import Decision
    from openclear.domain.exception_item import ExceptionItem
    from openclear.domain.issued_item import IssuedItem
    from openclear.domain.paid_item import PaidItem
    from openclear.domain.stop_payment import StopPayment

    allowed = list(ids)
    options = [with_loader_criteria(Account, lambda cls: cls.id.in_(allowed), include_aliases=True)]
    for model in (IssuedItem, StopPayment, PaidItem, AchAuthorizationRule, AchTransaction):
        options.append(with_loader_criteria(model, lambda cls: cls.account_id.in_(allowed), include_aliases=True))
    options.append(with_loader_criteria(ExceptionItem, lambda cls: or_(
        cls.source_item_id.in_(select(PaidItem.id).where(PaidItem.account_id.in_(allowed)).scalar_subquery()),
        cls.source_item_id.in_(select(AchTransaction.id).where(AchTransaction.account_id.in_(allowed)).scalar_subquery()),
    ), include_aliases=True))
    options.append(with_loader_criteria(Decision, lambda cls: cls.exception_item_id.in_(
        select(ExceptionItem.id).where(or_(
            ExceptionItem.source_item_id.in_(select(PaidItem.id).where(PaidItem.account_id.in_(allowed)).scalar_subquery()),
            ExceptionItem.source_item_id.in_(select(AchTransaction.id).where(AchTransaction.account_id.in_(allowed)).scalar_subquery()),
        )).scalar_subquery()), include_aliases=True))
    options.append(with_loader_criteria(CheckImage, lambda cls: cls.paid_item_id.in_(
        select(PaidItem.id).where(PaidItem.account_id.in_(allowed)).scalar_subquery()), include_aliases=True))
    options.append(with_loader_criteria(BulkUploadFile, lambda cls: cls.uploaded_by_user_id == user_id, include_aliases=True))
    options.append(with_loader_criteria(DataExportJob, lambda cls: cls.requested_by_user_id == user_id, include_aliases=True))
    return options


@event.listens_for(Session, "do_orm_execute")
def _limit_to_accounts(state: ORMExecuteState) -> None:
    ids = state.session.info.get(_KEY)
    if ids is None or not (state.is_select or state.is_update or state.is_delete):
        return
    if state.is_select and (state.is_column_load or state.is_relationship_load):
        return  # loading more of a row already in scope
    state.statement = state.statement.options(*_criteria(ids, state.session.info.get(_USER_KEY)))
