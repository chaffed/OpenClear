# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Everything a fee can be charged for (BILLING_PLAN.md): the metric catalog.

Each metric counts one thing in a billing period and says where each count lands:
- "account": against an account (and its customer). Per-item activity mostly lands here,
  so the analysis feed can report volumes per account.
- "customer": against a customer (no account), e.g. users, files received.
- "bank": against the bank as a whole, e.g. SMS sent (a notification isn't tied to a
  customer). These only appear on the bank-wide statement.

Monthly ("census") metrics count what exists at the end of the period (BILLING_PLAN.md
B-D6). Accounts have no creation date or active flag, so every current account counts.
Per-item metrics count activity inside the period. Fraud-training examples are never
counted: they aren't real activity.
"""

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Literal

from sqlalchemy import distinct, func, or_, select
from sqlalchemy.orm import Session

from openclear.domain.account import Account, AchDebitBlockMode
from openclear.domain.ach_authorization_rule import AchAuthorizationRule, AchAuthorizationStatus
from openclear.domain.ach_transaction import AchTransaction, AchTransactionSource, AchTransactionType
from openclear.domain.bulk_upload_file import BulkUploadFile, BulkUploadKind
from openclear.domain.check_image import CheckImage, OcrStatus
from openclear.domain.customer import Customer
from openclear.domain.data_export_job import DataExportJob
from openclear.domain.decision import Decision, DecisionOutcome, DecisionSource
from openclear.domain.exception_item import ExceptionItem, ExceptionItemSource
from openclear.domain.issued_item import IssuedItem
from openclear.domain.ml_model import MlModel, MlModelStatus
from openclear.domain.notification import Notification, NotificationChannel, NotificationStatus
from openclear.domain.paid_item import PaidItem, PaidItemSource
from openclear.domain.stop_payment import StopPayment
from openclear.domain.tenant_membership import TenantMembership
from openclear.domain.wsud_statement import WsudStatement


@dataclass(frozen=True, slots=True)
class Period:
    start: date
    end: date  # inclusive

    @property
    def start_at(self) -> datetime:
        return datetime.combine(self.start, time.min, tzinfo=timezone.utc)

    @property
    def end_before(self) -> datetime:
        """The first instant after the period (end is inclusive)."""
        return datetime.combine(self.end + timedelta(days=1), time.min, tzinfo=timezone.utc)


@dataclass(frozen=True, slots=True)
class Volume:
    customer_id: uuid.UUID | None
    account_id: uuid.UUID | None
    quantity: int


Counter = Callable[[Session, uuid.UUID, Period], list[Volume]]


@dataclass(frozen=True, slots=True)
class Metric:
    code: str
    label: str
    unit: Literal["month", "item"]
    level: Literal["account", "customer", "bank"]
    count: Counter


def _rows(session: Session, stmt, level: str) -> list[Volume]:
    out = []
    for row in session.execute(stmt).all():
        if level == "account":
            out.append(Volume(row[0], row[1], int(row[2])))
        elif level == "customer":
            out.append(Volume(row[0], None, int(row[1])))
        else:
            out.append(Volume(None, None, int(row[0])))
    return [v for v in out if v.quantity]


def _in(column, period: Period):
    return (column >= period.start_at, column < period.end_before)


# --- Monthly (census) --------------------------------------------------------------------


def _customers(session, tenant_id, period):
    stmt = (
        select(Customer.id, func.count())
        .where(Customer.tenant_id == tenant_id, Customer.is_active.is_(True), Customer.created_at < period.end_before)
        .group_by(Customer.id)
    )
    return _rows(session, stmt, "customer")


def _accounts(session, tenant_id, period):
    stmt = (
        select(Account.customer_id, Account.id, func.count())
        .where(Account.tenant_id == tenant_id)
        .group_by(Account.customer_id, Account.id)
    )
    return _rows(session, stmt, "account")


def _ach_accounts(session, tenant_id, period):
    """Accounts with ACH positive pay in use: a full debit block, or at least one active
    authorization rule (the debit filter)."""
    with_rules = select(AchAuthorizationRule.account_id).where(
        AchAuthorizationRule.tenant_id == tenant_id, AchAuthorizationRule.status == AchAuthorizationStatus.ACTIVE
    )
    stmt = (
        select(Account.customer_id, Account.id, func.count())
        .where(
            Account.tenant_id == tenant_id,
            or_(Account.ach_debit_block_mode == AchDebitBlockMode.BLOCK_ALL, Account.id.in_(with_rules)),
        )
        .group_by(Account.customer_id, Account.id)
    )
    return _rows(session, stmt, "account")


def _users(session, tenant_id, period):
    """Distinct people with an active membership, per customer (bank staff, with no
    customer, land on the bank-wide statement)."""
    stmt = (
        select(TenantMembership.customer_id, func.count(distinct(TenantMembership.user_id)))
        .where(
            TenantMembership.tenant_id == tenant_id,
            TenantMembership.is_active.is_(True),
            TenantMembership.created_at < period.end_before,
        )
        .group_by(TenantMembership.customer_id)
    )
    return _rows(session, stmt, "customer")


def _ach_rules(session, tenant_id, period):
    stmt = (
        select(AchAuthorizationRule.customer_id, AchAuthorizationRule.account_id, func.count())
        .where(
            AchAuthorizationRule.tenant_id == tenant_id,
            AchAuthorizationRule.status == AchAuthorizationStatus.ACTIVE,
            AchAuthorizationRule.created_at < period.end_before,
        )
        .group_by(AchAuthorizationRule.customer_id, AchAuthorizationRule.account_id)
    )
    return _rows(session, stmt, "account")


def _customer_models(session, tenant_id, period):
    stmt = (
        select(MlModel.customer_id, func.count(distinct(MlModel.network_code)))
        .where(MlModel.tenant_id == tenant_id, MlModel.customer_id.is_not(None), MlModel.status == MlModelStatus.ACTIVE)
        .group_by(MlModel.customer_id)
    )
    return _rows(session, stmt, "customer")


# --- Per item --------------------------------------------------------------------------


def _issued_items(session, tenant_id, period):
    stmt = (
        select(IssuedItem.customer_id, IssuedItem.account_id, func.count())
        .where(IssuedItem.tenant_id == tenant_id, *_in(IssuedItem.created_at, period))
        .group_by(IssuedItem.customer_id, IssuedItem.account_id)
    )
    return _rows(session, stmt, "account")


def _files(kinds: tuple[BulkUploadKind, ...] | None) -> Counter:
    def count(session, tenant_id, period):
        stmt = (
            select(BulkUploadFile.customer_id, func.count())
            .where(
                BulkUploadFile.tenant_id == tenant_id,
                *_in(BulkUploadFile.uploaded_at, period),
                BulkUploadFile.kind != BulkUploadKind.FRAUD_TRAINING_EXAMPLES,
            )
            .group_by(BulkUploadFile.customer_id)
        )
        if kinds is not None:
            stmt = stmt.where(BulkUploadFile.kind.in_(kinds))
        return _rows(session, stmt, "customer")

    return count


def _presented_checks(session, tenant_id, period):
    stmt = (
        select(PaidItem.customer_id, PaidItem.account_id, func.count())
        .where(PaidItem.tenant_id == tenant_id, PaidItem.source != PaidItemSource.TRAINING_BACKFILL, *_in(PaidItem.created_at, period))
        .group_by(PaidItem.customer_id, PaidItem.account_id)
    )
    return _rows(session, stmt, "account")


def _ach_debits(session, tenant_id, period):
    stmt = (
        select(AchTransaction.customer_id, AchTransaction.account_id, func.count())
        .where(
            AchTransaction.tenant_id == tenant_id,
            AchTransaction.transaction_type == AchTransactionType.DEBIT,
            AchTransaction.source != AchTransactionSource.TRAINING_BACKFILL,
            *_in(AchTransaction.created_at, period),
        )
        .group_by(AchTransaction.customer_id, AchTransaction.account_id)
    )
    return _rows(session, stmt, "account")


def _source_account(network_code: str):
    """The account column of an exception's source item, for joining."""
    return (PaidItem, PaidItem.account_id) if network_code == "check" else (AchTransaction, AchTransaction.account_id)


def _exceptions(network_code: str) -> Counter:
    def count(session, tenant_id, period):
        model, account_col = _source_account(network_code)
        stmt = (
            select(ExceptionItem.customer_id, account_col, func.count())
            .join(model, model.id == ExceptionItem.source_item_id)
            .where(
                ExceptionItem.tenant_id == tenant_id,
                ExceptionItem.network_code == network_code,
                ExceptionItem.source == ExceptionItemSource.LIVE,
                *_in(ExceptionItem.created_at, period),
            )
            .group_by(ExceptionItem.customer_id, account_col)
        )
        return _rows(session, stmt, "account")

    return count


def _decisions(networks: tuple[str, ...], *criteria) -> Counter:
    def count(session, tenant_id, period):
        out: list[Volume] = []
        for network_code in networks:
            model, account_col = _source_account(network_code)
            stmt = (
                select(ExceptionItem.customer_id, account_col, func.count())
                .select_from(Decision)
                .join(ExceptionItem, ExceptionItem.id == Decision.exception_item_id)
                .join(model, model.id == ExceptionItem.source_item_id)
                .where(
                    Decision.tenant_id == tenant_id,
                    ExceptionItem.network_code == network_code,
                    ExceptionItem.source == ExceptionItemSource.LIVE,
                    *_in(Decision.decided_at, period),
                    *criteria,
                )
                .group_by(ExceptionItem.customer_id, account_col)
            )
            out += _rows(session, stmt, "account")
        return out

    return count


def _stop_payments(session, tenant_id, period):
    stmt = (
        select(StopPayment.customer_id, StopPayment.account_id, func.count())
        .where(StopPayment.tenant_id == tenant_id, *_in(StopPayment.created_at, period))
        .group_by(StopPayment.customer_id, StopPayment.account_id)
    )
    return _rows(session, stmt, "account")


def _check_images(session, tenant_id, period):
    stmt = (
        select(CheckImage.customer_id, PaidItem.account_id, func.count())
        .select_from(CheckImage)
        .outerjoin(PaidItem, PaidItem.id == CheckImage.paid_item_id)
        .where(CheckImage.tenant_id == tenant_id, CheckImage.ocr_status == OcrStatus.COMPLETED, *_in(CheckImage.processed_at, period))
        .group_by(CheckImage.customer_id, PaidItem.account_id)
    )
    return _rows(session, stmt, "account")


def _wsud(session, tenant_id, period):
    stmt = (
        select(WsudStatement.customer_id, func.count())
        .where(WsudStatement.tenant_id == tenant_id, *_in(WsudStatement.signed_at, period))
        .group_by(WsudStatement.customer_id)
    )
    return _rows(session, stmt, "customer")


def _exports(session, tenant_id, period):
    stmt = (
        select(DataExportJob.customer_id, func.count())
        .where(DataExportJob.tenant_id == tenant_id, *_in(DataExportJob.requested_at, period))
        .group_by(DataExportJob.customer_id)
    )
    return _rows(session, stmt, "customer")


def _notifications(channel: NotificationChannel) -> Counter:
    def count(session, tenant_id, period):
        stmt = select(func.count()).where(
            Notification.tenant_id == tenant_id,
            Notification.channel == channel,
            Notification.status == NotificationStatus.SENT,
            *_in(Notification.sent_at, period),
        )
        return _rows(session, stmt, "bank")

    return count


METRICS: dict[str, Metric] = {m.code: m for m in [
    # Monthly
    Metric("customer_monthly", "Customers enrolled (per month)", "month", "customer", _customers),
    Metric("account_monthly", "Accounts enrolled (per month)", "month", "account", _accounts),
    Metric("ach_account_monthly", "Accounts with ACH positive pay (per month)", "month", "account", _ach_accounts),
    Metric("user_monthly", "Users with access (per month)", "month", "customer", _users),
    Metric("ach_rule_monthly", "Active ACH authorization rules (per month)", "month", "account", _ach_rules),
    Metric("customer_model_monthly", "Customer-specific fraud models (per month)", "month", "customer", _customer_models),
    # Check positive pay
    Metric("issued_items", "Issued checks loaded", "item", "account", _issued_items),
    Metric("issue_files", "Issued-check files received", "item", "customer", _files((BulkUploadKind.ISSUED_ITEMS,))),
    Metric("presented_checks", "Checks presented (evaluated)", "item", "account", _presented_checks),
    Metric("check_exceptions", "Check exceptions", "item", "account", _exceptions("check")),
    Metric("check_returns", "Checks returned", "item", "account",
           _decisions(("check",), Decision.outcome == DecisionOutcome.RETURN)),
    Metric("stop_payments", "Stop payments placed", "item", "account", _stop_payments),
    Metric("check_images", "Check images processed (payee positive pay)", "item", "account", _check_images),
    # ACH positive pay
    Metric("ach_debits", "ACH debits evaluated", "item", "account", _ach_debits),
    Metric("ach_exceptions", "ACH exceptions", "item", "account", _exceptions("ach")),
    Metric("ach_returns", "ACH debits returned", "item", "account",
           _decisions(("ach",), Decision.outcome == DecisionOutcome.RETURN)),
    Metric("wsud_statements", "Unauthorized-debit statements (WSUD) signed", "item", "customer", _wsud),
    # Either network
    Metric("items_paid_on_decision", "Exceptions decided pay", "item", "account",
           _decisions(("check", "ach"), Decision.outcome == DecisionOutcome.PAY)),
    Metric("items_returned", "Exceptions decided return (check and ACH)", "item", "account",
           _decisions(("check", "ach"), Decision.outcome == DecisionOutcome.RETURN)),
    Metric("auto_decisions", "Exceptions decided automatically (default disposition)", "item", "account",
           _decisions(("check", "ach"), Decision.source != DecisionSource.HUMAN)),
    Metric("files_received", "Files received (any kind)", "item", "customer", _files(None)),
    Metric("data_exports", "Data exports", "item", "customer", _exports),
    # Bank-wide only
    Metric("sms_sent", "SMS notifications sent", "item", "bank", _notifications(NotificationChannel.SMS)),
    Metric("email_sent", "Email notifications sent", "item", "bank", _notifications(NotificationChannel.EMAIL)),
]}
