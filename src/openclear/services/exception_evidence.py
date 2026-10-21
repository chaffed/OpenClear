# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""What a reviewer needs on screen to decide an exception (FIX_PLAN.md Phase 6, U2): what
was expected next to what was presented, with the fields that don't match flagged, plus
the surrounding facts (a stop payment, the earlier payment of a duplicate, the check
image, the account and customer, and what happens if nobody decides in time).

Every lookup goes through the caller's customer-scoped repositories, so a customer's own
staff can never see another customer's records here. Values stay raw (Decimal, date);
the template formats them."""

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from openclear.domain.customer import Customer
from openclear.domain.customer_disposition_setting import DispositionMode
from openclear.domain.exception_item import ExceptionItem
from openclear.domain.stop_payment import StopPaymentStatus
from openclear.repositories.account_repo import AccountRepository
from openclear.repositories.ach_authorization_repo import AchAuthorizationRepository
from openclear.repositories.check_image_repo import CheckImageRepository
from openclear.repositories.issued_item_repo import IssuedItemRepository
from openclear.repositories.paid_item_repo import PaidItemRepository
from openclear.repositories.stop_payment_repo import StopPaymentRepository
from openclear.services import auto_disposition_service

Value = Decimal | date | str | None


@dataclass(frozen=True, slots=True)
class EvidenceRow:
    label: str
    expected: Value
    presented: Value
    mismatch: bool = False
    kind: str = "text"  # "money" | "date" | "text" — how the template formats both columns


@dataclass(slots=True)
class Evidence:
    expected_heading: str
    presented_heading: str
    rows: list[EvidenceRow] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    account_label: str | None = None
    customer_name: str | None = None
    check_image: Any = None  # CheckImage | None
    paid_item_id: uuid.UUID | None = None
    deadline: datetime | None = None
    deadline_outcome: str | None = None  # what happens at the deadline, in words


_DEADLINE_OUTCOMES = {
    DispositionMode.FIXED_PAY: "it will be paid automatically",
    DispositionMode.FIXED_RETURN: "it will be returned automatically",
}


def _types(item: ExceptionItem) -> set[str]:
    return set(item.exception_types.split(",")) if item.exception_types else set()


def _account_and_customer(session: Session, item: ExceptionItem, scope: uuid.UUID | None, account_id, evidence: Evidence):
    account = AccountRepository(session, item.tenant_id, scope).get(account_id)
    if account is not None:
        evidence.account_label = f"{account.account_number} ({account.name})"
    if item.customer_id is not None:
        customer = session.get(Customer, item.customer_id)
        evidence.customer_name = customer.name if customer is not None and customer.tenant_id == item.tenant_id else None


def _deadline(session: Session, item: ExceptionItem, evidence: Evidence) -> None:
    """Every item has a cut-off since 1.26.0, the bank's own items too; only a customer's has a
    default decision to describe."""
    if item.decision_deadline is None:
        return
    evidence.deadline = item.decision_deadline
    if item.customer_id is not None:
        mode = auto_disposition_service.get_disposition_mode(session, item.tenant_id, item.customer_id, item.network_code)
        evidence.deadline_outcome = _DEADLINE_OUTCOMES.get(mode)


def _check_evidence(session: Session, item: ExceptionItem, paid, scope: uuid.UUID | None) -> Evidence:
    types = _types(item)
    evidence = Evidence(expected_heading="Issued", presented_heading="Presented", paid_item_id=paid.id)
    _account_and_customer(session, item, scope, paid.account_id, evidence)

    images = CheckImageRepository(session, item.tenant_id, scope).list(paid_item_id=paid.id)
    evidence.check_image = images[0] if images else None
    ocr_payee = evidence.check_image.ocr_extracted_payee if evidence.check_image else None

    issued = IssuedItemRepository(session, item.tenant_id, scope).get(item.related_reference_id) if item.related_reference_id else None
    if issued is None:
        # "Couldn't be found" only when the exception points at an issued check that's no
        # longer visible; otherwise there simply isn't one on file (e.g. a stop payment
        # placed on a check that was never in an issued file).
        evidence.notes.append(
            "The matching issued check couldn't be found."
            if item.related_reference_id else
            "No issued check with this number is on file for this account, so there's nothing to compare it with."
        )
    evidence.rows = [
        EvidenceRow("Check number", issued.check_number if issued else None, paid.check_number),
        EvidenceRow("Amount", issued.amount if issued else None, paid.presented_amount,
                    mismatch="amount_mismatch" in types, kind="money"),
        EvidenceRow("Payee", issued.payee_name if issued else None,
                    f"{ocr_payee} (read from the check image)" if ocr_payee else "Not read from an image",
                    mismatch="payee_mismatch" in types),
        EvidenceRow("Date", issued.issue_date if issued else None, paid.presented_date,
                    mismatch="stale_dated" in types, kind="date"),
    ]
    if issued is not None and "stale_dated" in types:
        evidence.notes.append(f"Presented {(paid.presented_date - issued.issue_date).days} days after it was issued.")
    if issued is not None and issued.status.value == "voided":
        evidence.notes.append(f"The issued check was voided{f': {issued.void_reason}' if issued.void_reason else ''}.")

    if "stopped" in types:
        stops = [s for s in StopPaymentRepository(session, item.tenant_id, scope).list(
            account_id=paid.account_id, check_number=paid.check_number) if s.status == StopPaymentStatus.ACTIVE]
        for stop in stops:
            evidence.notes.append(
                f"A stop payment is in effect from {stop.effective_date:%Y-%m-%d}"
                + (f" ({stop.reason.rstrip('. ')})" if stop.reason else "") + "."
            )
    if "duplicate_paid" in types:
        earlier = [p for p in PaidItemRepository(session, item.tenant_id, scope).list(
            account_id=paid.account_id, check_number=paid.check_number) if p.id != paid.id]
        for other in earlier:
            evidence.notes.append(
                f"This check number was already presented on {other.presented_date:%Y-%m-%d} for ${other.presented_amount:,.2f}."
            )
    return evidence


def _ach_evidence(session: Session, item: ExceptionItem, txn, scope: uuid.UUID | None) -> Evidence:
    types = _types(item)
    evidence = Evidence(expected_heading="Authorized", presented_heading="This transaction")
    _account_and_customer(session, item, scope, txn.account_id, evidence)

    rule = AchAuthorizationRepository(session, item.tenant_id, scope).get(item.related_reference_id) if item.related_reference_id else None
    if rule is None:
        evidence.notes.append(
            f"No ACH authorization is on file for {txn.originator_name} ({txn.originator_id}) on this account, "
            "or the account blocks all ACH debits."
        )
    elif "receiver_id_not_permitted" in types:
        evidence.notes.append("This originator is authorized on the account, but not for this receiver ID.")

    def sec_codes(codes):
        return ", ".join(codes) if codes else "Any"

    evidence.rows = [
        EvidenceRow("Originator", f"{rule.originator_name} ({rule.originator_id})" if rule else None,
                    f"{txn.originator_name} ({txn.originator_id})", mismatch="unauthorized_originator" in types),
        EvidenceRow("Receiver ID", (rule.receiver_id or "Any") if rule else None, txn.receiver_id or "—",
                    mismatch="receiver_id_not_permitted" in types),
        EvidenceRow("Amount", (rule.max_amount if rule and rule.max_amount is not None else ("No limit" if rule else None)),
                    txn.amount, mismatch="amount_exceeds_limit" in types, kind="money"),
        EvidenceRow("SEC code", sec_codes(rule.allowed_sec_codes) if rule else None, txn.sec_code,
                    mismatch="sec_code_not_permitted" in types),
        EvidenceRow("Frequency", (f"Up to {rule.frequency_limit} per period" if rule.frequency_limit else "No limit") if rule else None,
                    "Over the limit" if "frequency_exceeded" in types else "Within the limit",
                    mismatch="frequency_exceeded" in types),
        EvidenceRow("Effective date", None, txn.effective_date, kind="date"),
        EvidenceRow("Trace number", None, txn.trace_number),
    ]
    return evidence


def build_evidence(session: Session, item: ExceptionItem, source_item, *, scope_customer_id: uuid.UUID | None) -> Evidence | None:
    """`scope_customer_id` is the viewer's own customer scope (ctx.customer_id)."""
    if source_item is None:
        return None
    if item.network_code == "check":
        evidence = _check_evidence(session, item, source_item, scope_customer_id)
    elif item.network_code == "ach":
        evidence = _ach_evidence(session, item, source_item, scope_customer_id)
    else:
        return None
    _deadline(session, item, evidence)
    return evidence
