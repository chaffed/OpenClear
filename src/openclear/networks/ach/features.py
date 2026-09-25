# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

from typing import Any

from sqlalchemy.orm import Session

from openclear.domain.ach_transaction import AchTransaction, AchTransactionType
from openclear.domain.exception_item import ExceptionItem


def build_ach_features(session: Session, exception_item: ExceptionItem) -> dict[str, Any]:
    """Feature vector for the ACH ML model — deliberately a different shape than the
    check model's (see docs/ARCHITECTURE.md, "ML pipeline", on why these are separate models
    rather than one shared one with zero-filled irrelevant fields)."""
    txn = session.get(AchTransaction, exception_item.source_item_id)
    exception_types = exception_item.exception_types.split(",") if exception_item.exception_types else []

    return {
        "amount": float(txn.amount) if txn else 0.0,
        "sec_code": txn.sec_code if txn else "",
        "has_receiver_id": bool(txn.receiver_id) if txn else False,
        "is_debit": txn.transaction_type == AchTransactionType.DEBIT if txn else False,
        "is_unauthorized_originator": "unauthorized_originator" in exception_types,
        "is_receiver_id_not_permitted": "receiver_id_not_permitted" in exception_types,
        "is_amount_exceeds_limit": "amount_exceeds_limit" in exception_types,
        "is_frequency_exceeded": "frequency_exceeded" in exception_types,
        "is_sec_code_not_permitted": "sec_code_not_permitted" in exception_types,
    }
