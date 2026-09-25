# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import uuid
from dataclasses import dataclass, field
from typing import Any, Protocol

from sqlalchemy.orm import Session

from openclear.domain.payment_network import SettlementTiming

__all__ = ["SettlementTiming", "MatchResult", "NetworkAdapter"]


@dataclass(frozen=True, slots=True)
class MatchResult:
    matched: bool
    exception_types: list[str] = field(default_factory=list)
    related_reference_id: uuid.UUID | None = None


class NetworkAdapter(Protocol):
    """Everything a payment network plugs into the shared exception/decision/ML core
    through. Shared code (services/exception_service, ml/predict, api/v1/exceptions.py)
    only ever imports `networks.registry`, never a concrete network module — so adding a
    new network (e.g. RTP) means writing one of these plus a `register_adapter()` call,
    with no changes to exception_item, decision, ml/, or the /exceptions API.

    `settlement_timing` is the forward-looking hook for real-time/irrevocable networks:
    ASYNC_REVIEWABLE networks (check, ach) create an exception_item and let a human review
    it within a window before final settlement. A SYNCHRONOUS_AUTHORIZATION network (e.g.
    RTP) would need its evaluate() called inline in the submission request path, with the
    ingestion API blocking on the result rather than accepting-and-queuing — that's a
    different call pattern at the ingestion service, not a schema change here.
    """

    code: str
    settlement_timing: SettlementTiming

    def evaluate(self, session: Session, transaction: Any) -> MatchResult:
        """Run this network's matching rules against a freshly-ingested transaction."""
        ...

    def build_features(self, session: Session, exception_item: Any) -> dict[str, Any]:
        """Build the ML feature vector for an exception raised by this network."""
        ...

    def load_source_item(self, session: Session, source_item_id: uuid.UUID) -> Any:
        """Resolve exception_item.source_item_id back to this network's transaction row.
        This is the only sanctioned way to dereference that pointer (no DB FK backs it)."""
        ...
