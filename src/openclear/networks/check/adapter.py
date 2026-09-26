# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from openclear.config import get_settings
from openclear.domain.check_image import CheckImage, OcrStatus
from openclear.domain.exception_item import ExceptionItem
from openclear.domain.issued_item import IssuedItem
from openclear.domain.paid_item import PaidItem, PaidItemSettlementStatus
from openclear.domain.payment_network import SettlementTiming
from openclear.domain.stop_payment import StopPayment, StopPaymentStatus
from openclear.domain.tenant import Tenant
from openclear.networks.base import MatchResult
from openclear.networks.check.rules import evaluate_check_rules
from openclear.networks.check.types import CheckMatchInputs, IssuedItemSnapshot


class CheckAdapter:
    code = "check"
    settlement_timing = SettlementTiming.ASYNC_REVIEWABLE

    def evaluate(self, session: Session, transaction: PaidItem) -> MatchResult:
        paid_item = transaction
        tenant = session.get(Tenant, paid_item.tenant_id)

        candidate = self._find_candidate_issued_item(session, paid_item)
        is_duplicate = self._is_duplicate_paid(session, paid_item)
        active_stop = self._find_active_stop(session, paid_item)
        check_image = self._find_completed_ocr(session, paid_item)

        settings = get_settings()
        inputs = CheckMatchInputs(
            presented_amount=paid_item.presented_amount,
            presented_date=paid_item.presented_date,
            is_duplicate_paid=is_duplicate,
            active_stop_found=active_stop is not None,
            candidate_issued_item=self._snapshot(candidate),
            stale_date_threshold_days=tenant.stale_date_threshold_days,
            payee_fuzzy_threshold=settings.payee_match_fuzzy_threshold,
            ocr_payee=check_image.ocr_extracted_payee if check_image else None,
            ocr_confidence=check_image.ocr_confidence if check_image else None,
        )

        exception_types, related_id = evaluate_check_rules(inputs)
        return MatchResult(
            matched=not exception_types,
            exception_types=[e.value for e in exception_types],
            related_reference_id=related_id,
        )

    def build_features(self, session: Session, exception_item: ExceptionItem) -> dict[str, Any]:
        from openclear.networks.check.features import build_check_features

        return build_check_features(session, exception_item)

    def load_source_item(self, session: Session, source_item_id: uuid.UUID) -> PaidItem | None:
        return session.get(PaidItem, source_item_id)

    def load_source_items(self, session: Session, source_item_ids: list[uuid.UUID]) -> dict[uuid.UUID, PaidItem]:
        if not source_item_ids:
            return {}
        rows = session.execute(select(PaidItem).where(PaidItem.id.in_(source_item_ids))).scalars()
        return {row.id: row for row in rows}

    @staticmethod
    def _snapshot(issued_item: IssuedItem | None) -> IssuedItemSnapshot | None:
        if issued_item is None:
            return None
        return IssuedItemSnapshot(
            id=issued_item.id,
            amount=issued_item.amount,
            payee_name=issued_item.payee_name,
            issue_date=issued_item.issue_date,
            status=issued_item.status.value,
        )

    @staticmethod
    def _find_candidate_issued_item(session: Session, paid_item: PaidItem) -> IssuedItem | None:
        stmt = select(IssuedItem).where(
            IssuedItem.tenant_id == paid_item.tenant_id,
            IssuedItem.account_id == paid_item.account_id,
            IssuedItem.check_number == paid_item.check_number,
        )
        return session.execute(stmt).scalar_one_or_none()

    @staticmethod
    def _is_duplicate_paid(session: Session, paid_item: PaidItem) -> bool:
        stmt = select(PaidItem.id).where(
            PaidItem.tenant_id == paid_item.tenant_id,
            PaidItem.account_id == paid_item.account_id,
            PaidItem.check_number == paid_item.check_number,
            PaidItem.presented_amount == paid_item.presented_amount,
            PaidItem.settlement_status == PaidItemSettlementStatus.PAID,
            PaidItem.id != paid_item.id,
        )
        return session.execute(stmt).first() is not None

    @staticmethod
    def _find_completed_ocr(session: Session, paid_item: PaidItem) -> CheckImage | None:
        stmt = select(CheckImage).where(
            CheckImage.paid_item_id == paid_item.id, CheckImage.ocr_status == OcrStatus.COMPLETED
        )
        return session.execute(stmt).scalars().first()

    @staticmethod
    def _find_active_stop(session: Session, paid_item: PaidItem) -> StopPayment | None:
        stmt = select(StopPayment).where(
            StopPayment.tenant_id == paid_item.tenant_id,
            StopPayment.account_id == paid_item.account_id,
            StopPayment.check_number == paid_item.check_number,
            StopPayment.status == StopPaymentStatus.ACTIVE,
            StopPayment.effective_date <= paid_item.presented_date,
        )
        stops = session.execute(stmt).scalars().all()
        for stop in stops:
            if stop.expiration_date is None or stop.expiration_date >= paid_item.presented_date:
                return stop
        return None
