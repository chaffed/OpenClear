# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Teller positive pay (TELLER_PLAN.md): a teller platform verifies a check before cash is paid
at a branch, then records it cashed so the same check can't be paid again elsewhere.

Verify runs the clearing rules (networks/check/rules.py) over the check as if it were being
presented today, with the issued item locked as clearing locks it (FR2), so a branch and
clearing can't disagree about a check, or both see it unpaid. It never scores. Matching-rule
plugins aren't run: they read a presented item, and nothing is presented until it's cashed.

Cashed claims the verification once (a conditional update), then presents the check as a paid
item with source `teller`; clearing's own presentment of it later is `duplicate_paid`."""

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from openclear.domain.account import Account, CheckReviewMode
from openclear.domain.paid_item import PaidItem
from openclear.domain.teller_verification import TellerVerification
from openclear.networks.check.adapter import CheckAdapter
from openclear.networks.check.rules import evaluate_check_rules
from openclear.networks.check.types import CheckExceptionType as T
from openclear.networks.check.types import CheckMatchInputs
from openclear.services import account_service, audit_log_service, security_events

VERIFICATION_MINUTES = 15
MATCH = "match"
NOT_ON_POSITIVE_PAY = "not_on_positive_pay"
# What a teller is told, by the clearing rule found (TL-D1), in the order the first problem is chosen.
RESULTS: dict[T, str] = {
    T.STOPPED: "stopped",
    T.DUPLICATE_PAID: "already_paid",
    T.VOIDED: "voided",
    T.NOT_IN_FILE: "not_issued",
    T.STOP_AMOUNT_MISMATCH: "stop_differs",
    T.AMOUNT_MISMATCH: "amount_differs",
    T.PAYEE_MISMATCH: "payee_differs",
    T.STALE_DATED: "stale",
    # A reverse positive pay account (REVERSE_POSITIVE_PAY_PLAN.md): no issued file, the customer reviews each check.
    T.REVIEW: "customer_review",
}
# The issued check names a payee and the teller didn't say what's on the check: the payee must be
# checked as written before cash is paid (the product owner, 2026-10-22).
PAYEE_NEEDED = "payee_needed"
# The order the first problem is chosen in: the clearing rules', then a missing payee.
_ORDER = [*RESULTS.values(), PAYEE_NEEDED]
# Not-issued answers from one teller, branch or client within an hour before it looks like
# someone trying check numbers (TL-D6).
PROBE_THRESHOLD = 10


class TellerError(ValueError):
    """Fit to show to the teller platform."""

    def __init__(self, message: str, status: int = 409):
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class VerifyRequest:
    account_number: str
    check_number: str
    amount: Decimal
    payee_name: str | None = None
    routing_number: str | None = None
    teller_id: str | None = None
    branch: str | None = None


@dataclass(frozen=True)
class Cashed:
    verification: TellerVerification
    paid_item: PaidItem
    # The presentment's own result: a check stopped, or paid elsewhere, between verify and cash.
    exception_types: list[str]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _client_id(session: Session, user_id: uuid.UUID | None) -> uuid.UUID | None:
    from openclear.domain.api_client import ApiClient

    if user_id is None:
        return None
    return session.execute(select(ApiClient.id).where(ApiClient.user_id == user_id)).scalar_one_or_none()


def _rules(session: Session, tenant_id: uuid.UUID, account: Account, request: VerifyRequest) -> tuple[list[T], object]:
    """The clearing rules over the check, as if presented today. Returns (problems, issued item)."""
    from openclear.config import get_settings
    from openclear.domain.tenant import Tenant

    today = _now().date()
    # The fields the clearing adapter's lookups read; the id is one no paid item has, so a
    # cashed or cleared copy of this check counts as already paid.
    presentment = SimpleNamespace(id=uuid.uuid4(), tenant_id=tenant_id, account_id=account.id, check_number=request.check_number,
                                  presented_amount=request.amount, presented_date=today)
    candidate = CheckAdapter._find_candidate_issued_item(session, presentment)  # locked, as clearing locks it (FR2)
    stop = CheckAdapter._find_active_stop(session, presentment)
    inputs = CheckMatchInputs(
        presented_amount=request.amount, presented_date=today,
        is_duplicate_paid=CheckAdapter._is_duplicate_paid(session, presentment),
        active_stop_found=stop is not None, active_stop_amount=stop.amount if stop is not None else None,
        candidate_issued_item=CheckAdapter._snapshot(candidate),
        stale_date_threshold_days=session.get(Tenant, tenant_id).stale_date_threshold_days,
        payee_fuzzy_threshold=get_settings().payee_match_fuzzy_threshold,
        ocr_payee=request.payee_name or None,  # the payee the teller read, compared as clearing compares OCR's
        review_all=account.check_review_mode == CheckReviewMode.REVERSE,
    )
    problems, _related = evaluate_check_rules(inputs)
    return problems, candidate


def _enrolled(session: Session, account: Account) -> bool:
    """On positive pay (or reverse): a bank's own account, or a customer's still enrolled. A
    customer deactivated isn't, and its checks are cashed by the bank's usual procedure."""
    from openclear.domain.customer import Customer

    if account.customer_id is None:
        return True
    customer = session.get(Customer, account.customer_id)
    return customer is not None and customer.is_active


def _names_a_payee(issued) -> bool:
    payee = (issued.payee_name or "").strip()
    return bool(payee) and payee != "(void)"


def verify(session: Session, tenant_id: uuid.UUID, request: VerifyRequest, *, user_id: uuid.UUID | None) -> TellerVerification:
    """Records and returns the verification (the caller commits). Raises nothing for a bad
    check: the answer is in `result`.

    An account not on positive pay (unknown, or its customer no longer enrolled) answers
    `not_on_positive_pay`: the teller cashes it by the bank's usual procedure. On an enrolled
    account a check that isn't found is declined (`not_issued`, or `customer_review` on reverse
    positive pay), and when the issued check names a payee, the teller must send the payee as
    written on the check, or it's declined (`payee_needed`)."""
    account = account_service.get_account_by_number(session, tenant_id, request.account_number)
    if account is None or not _enrolled(session, account):
        result, problems, candidate = NOT_ON_POSITIVE_PAY, [NOT_ON_POSITIVE_PAY], None
    else:
        found, candidate = _rules(session, tenant_id, account, request)
        problems = [RESULTS[t] for t in found]
        if candidate is not None and _names_a_payee(candidate) and not (request.payee_name or "").strip():
            problems.append(PAYEE_NEEDED)
        result = next((code for code in _ORDER if code in problems), MATCH)
    now = _now()
    verification = TellerVerification(
        tenant_id=tenant_id, account_id=account.id if account else None, customer_id=account.customer_id if account else None,
        routing_number=request.routing_number, account_number=request.account_number, check_number=request.check_number,
        amount=request.amount, payee_name=request.payee_name, result=result, problems=problems,
        issued_item_id=candidate.id if candidate is not None else None, teller_id=request.teller_id, branch=request.branch,
        api_client_id=_client_id(session, user_id), created_at=now, expires_at=now + timedelta(minutes=VERIFICATION_MINUTES),
    )
    session.add(verification)
    session.flush()
    where = f" at branch {request.branch}" if request.branch else ""
    audit_log_service.record_action(
        session, tenant_id, actor_user_id=user_id, channel="api",
        action="teller.verify" if result == MATCH else "teller.verify.declined",
        summary=(f"Check {request.check_number} on account {request.account_number} for {request.amount:,.2f} verified{where}: "
                 f"{'it can be cashed' if result == MATCH else 'declined, ' + result.replace('_', ' ')}")[:500],
        resource_type="teller_verification", resource_id=verification.id,
        metadata={"result": result, "problems": problems, "teller_id": request.teller_id, "branch": request.branch},
    )
    if result in ("not_issued", NOT_ON_POSITIVE_PAY):
        _watch_for_probing(session, tenant_id, verification)
    return verification


def _watch_for_probing(session: Session, tenant_id: uuid.UUID, verification: TellerVerification) -> None:
    """Many not-issued answers to one teller, branch or client within an hour looks like someone
    trying check numbers (TL-D6): a high security event, at most one a minute for each, named
    by the narrowest of them that crossed the line."""
    for column, value in (("teller_id", verification.teller_id), ("branch", verification.branch),
                          ("api_client_id", verification.api_client_id)):
        if value is None:
            continue
        misses = session.execute(select(func.count()).select_from(TellerVerification).where(
            TellerVerification.tenant_id == tenant_id, getattr(TellerVerification, column) == value,
            TellerVerification.result.in_(["not_issued", NOT_ON_POSITIVE_PAY]),
            TellerVerification.created_at >= _now() - timedelta(hours=1))).scalar_one()
        if misses >= PROBE_THRESHOLD:
            security_events.emit_sampled(
                session, "teller.verify.probing", key=f"{tenant_id}:{column}:{value}", tenant_id=tenant_id, outcome="failure",
                reason=f"{misses} checks not issued within an hour, by {column.replace('_', ' ')} {value}",
                details={column: str(value), "not_issued_last_hour": misses})
            return  # one alert for the call, by the narrowest of who asked


def cash(session: Session, tenant_id: uuid.UUID, verification_id: uuid.UUID, amount: Decimal, *,
         user_id: uuid.UUID | None) -> Cashed:
    """Claims a matching, unexpired verification once, then presents the check as paid at the
    branch. Raises TellerError (and changes nothing) otherwise. The caller commits."""
    from openclear.domain.exception_item import ExceptionItem
    from openclear.domain.paid_item import PaidItemSource
    from openclear.networks.check.ingestion import PaidItemSubmission, ingest_paid_item

    verification = session.get(TellerVerification, verification_id)
    if verification is None or verification.tenant_id != tenant_id:
        raise TellerError("Verification not found.", 404)
    if verification.result != MATCH:
        raise TellerError(f"That check was declined ({verification.result.replace('_', ' ')}), so it can't be recorded as cashed.")
    if amount != verification.amount:
        raise TellerError(f"The amount paid, {amount:,.2f}, isn't the amount verified, {verification.amount:,.2f}. Verify it again.")
    now = _now()
    claimed = session.execute(update(TellerVerification).where(
        TellerVerification.id == verification.id, TellerVerification.cashed_at.is_(None), TellerVerification.expires_at > now,
    ).values(cashed_at=now).execution_options(synchronize_session=False)).rowcount
    if claimed != 1:
        session.refresh(verification)
        if verification.cashed_at is not None:
            raise TellerError("That verification was already recorded as cashed.")
        raise TellerError(f"That verification expired ({VERIFICATION_MINUTES} minutes). Verify the check again.")
    session.refresh(verification)

    paid_item = ingest_paid_item(session, tenant_id, PaidItemSubmission(
        account_id=verification.account_id, check_number=verification.check_number, presented_amount=verification.amount,
        presented_date=now.date(), source=PaidItemSource.TELLER))
    verification.paid_item_id = paid_item.id
    exception = session.execute(select(ExceptionItem).where(ExceptionItem.source_item_id == paid_item.id)).scalar_one_or_none()
    types = [t for t in (exception.exception_types.split(",") if exception else []) if t]
    where = f" at branch {verification.branch}" if verification.branch else ""
    audit_log_service.record_action(
        session, tenant_id, actor_user_id=user_id, channel="api", action="teller.cashed",
        summary=f"Check {verification.check_number} on account {verification.account_number} cashed{where} for {amount:,.2f}"[:500],
        resource_type="paid_item", resource_id=paid_item.id,
        metadata={"verification_id": str(verification.id), "teller_id": verification.teller_id, "branch": verification.branch,
                  "exception_types": types},
    )
    session.flush()
    return Cashed(verification, paid_item, types)


def declined_recently(session: Session, tenant_id: uuid.UUID, customer_id: uuid.UUID | None, *, days: int = 7,
                      limit: int = 20) -> list[TellerVerification]:
    """Checks presented at a branch and declined, for the customer to know someone tried (TL-D4):
    information, with no decision to make."""
    stmt = select(TellerVerification).where(
        TellerVerification.tenant_id == tenant_id, TellerVerification.result != MATCH,
        TellerVerification.result != NOT_ON_POSITIVE_PAY, TellerVerification.created_at >= _now() - timedelta(days=days))
    if customer_id is not None:
        stmt = stmt.where(TellerVerification.customer_id == customer_id)
    return list(session.execute(stmt.order_by(TellerVerification.created_at.desc()).limit(limit)).scalars())
