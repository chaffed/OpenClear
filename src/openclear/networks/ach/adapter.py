# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import uuid
from datetime import timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from openclear.domain.account import Account, AchDebitBlockMode
from openclear.domain.ach_authorization_rule import AchAuthorizationRule, AchAuthorizationStatus
from openclear.domain.ach_transaction import AchSettlementStatus, AchTransaction, AchTransactionType
from openclear.domain.exception_item import ExceptionItem
from openclear.domain.payment_network import SettlementTiming
from openclear.networks.ach.rules import evaluate_ach_rules
from openclear.networks.ach.types import AchMatchInputs, AuthorizationSnapshot
from openclear.networks.base import MatchResult

_FREQUENCY_WINDOW_DAYS = 30


class AchAdapter:
    code = "ach"
    settlement_timing = SettlementTiming.ASYNC_REVIEWABLE

    def evaluate(self, session: Session, transaction: AchTransaction) -> MatchResult:
        txn = transaction
        account = session.get(Account, txn.account_id)
        is_debit = txn.transaction_type == AchTransactionType.DEBIT

        candidates = self._find_candidate_authorizations(session, txn) if is_debit else []
        debit_count = self._count_prior_settled_debits(session, txn) if is_debit else 0

        inputs = AchMatchInputs(
            is_debit=is_debit,
            debit_block_all=account.ach_debit_block_mode == AchDebitBlockMode.BLOCK_ALL,
            amount=txn.amount,
            sec_code=txn.sec_code,
            receiver_id=txn.receiver_id,
            candidate_authorizations=[self._snapshot(c) for c in candidates],
            debit_count_this_period=debit_count,
        )

        exception_types, related_id = evaluate_ach_rules(inputs)
        result = MatchResult(
            matched=not exception_types,
            exception_types=[e.value for e in exception_types],
            related_reference_id=related_id,
        )
        # Matching-rule plugins the bank turned on can add exception types, never clear one.
        from openclear.services.plugin_service import apply_matching_rules

        return apply_matching_rules(session, self.code, txn, result)

    def build_features(self, session: Session, exception_item: ExceptionItem) -> dict[str, Any]:
        from openclear.networks.ach.features import build_ach_features

        from openclear.services.plugin_service import extra_features

        return {**build_ach_features(session, exception_item), **extra_features(session, self.code, exception_item)}

    def load_source_item(self, session: Session, source_item_id: uuid.UUID) -> AchTransaction | None:
        return session.get(AchTransaction, source_item_id)

    def load_source_items(self, session: Session, source_item_ids: list[uuid.UUID]) -> dict[uuid.UUID, AchTransaction]:
        if not source_item_ids:
            return {}
        rows = session.execute(select(AchTransaction).where(AchTransaction.id.in_(source_item_ids))).scalars()
        return {row.id: row for row in rows}

    @staticmethod
    def _snapshot(auth: AchAuthorizationRule) -> AuthorizationSnapshot:
        return AuthorizationSnapshot(
            id=auth.id,
            receiver_id=auth.receiver_id,
            max_amount=auth.max_amount,
            frequency_limit=auth.frequency_limit,
            allowed_sec_codes=auth.allowed_sec_codes,
        )

    @staticmethod
    def _find_candidate_authorizations(session: Session, txn: AchTransaction) -> list[AchAuthorizationRule]:
        """Every active, in-window rule for this (account, originator_id) — deliberately
        NOT filtered by receiver_id here; rules.py picks the most specific match (exact
        receiver_id beats a wildcard rule) so that decision stays a pure, testable function."""
        stmt = select(AchAuthorizationRule).where(
            AchAuthorizationRule.tenant_id == txn.tenant_id,
            AchAuthorizationRule.account_id == txn.account_id,
            AchAuthorizationRule.originator_id == txn.originator_id,
            AchAuthorizationRule.status == AchAuthorizationStatus.ACTIVE,
            AchAuthorizationRule.effective_date <= txn.effective_date,
        )
        rules = session.execute(stmt).scalars().all()
        return [r for r in rules if r.expiration_date is None or r.expiration_date >= txn.effective_date]

    @staticmethod
    def _count_prior_settled_debits(session: Session, txn: AchTransaction) -> int:
        window_start = txn.effective_date - timedelta(days=_FREQUENCY_WINDOW_DAYS)
        stmt = select(func.count()).select_from(AchTransaction).where(
            AchTransaction.tenant_id == txn.tenant_id,
            AchTransaction.account_id == txn.account_id,
            AchTransaction.originator_id == txn.originator_id,
            AchTransaction.transaction_type == AchTransactionType.DEBIT,
            AchTransaction.settlement_status == AchSettlementStatus.PAID,
            AchTransaction.effective_date >= window_start,
            AchTransaction.effective_date < txn.effective_date,
            AchTransaction.id != txn.id,
        )
        return session.execute(stmt).scalar_one()
