# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import re
import uuid

from rapidfuzz import fuzz

from openclear.networks.check.types import CheckExceptionType, CheckMatchInputs

_NON_ALNUM = re.compile(r"[^A-Z0-9 ]")


def normalize_payee(name: str) -> str:
    return _NON_ALNUM.sub("", name.upper()).strip()


def evaluate_check_rules(inputs: CheckMatchInputs) -> tuple[list[CheckExceptionType], uuid.UUID | None]:
    """Pure function: no DB session, only pre-loaded facts about the presented item and
    its candidate issued_item. Order matters — see docs/ARCHITECTURE.md ("Matching rules")
    for the reasoning behind each rule's position and short-circuits."""
    exceptions: list[CheckExceptionType] = []

    if inputs.is_duplicate_paid:
        exceptions.append(CheckExceptionType.DUPLICATE_PAID)

    if inputs.active_stop_found:
        if inputs.active_stop_amount is None or inputs.active_stop_amount == inputs.presented_amount:
            exceptions.append(CheckExceptionType.STOPPED)
            related_id = inputs.candidate_issued_item.id if inputs.candidate_issued_item else None
            return exceptions, related_id
        # A stop for another amount: maybe not this check. It's still held for a person, with
        # the usual checks below so they see everything else that's wrong too.
        exceptions.append(CheckExceptionType.STOP_AMOUNT_MISMATCH)

    candidate = inputs.candidate_issued_item
    if candidate is None:
        exceptions.append(CheckExceptionType.NOT_IN_FILE)
        return exceptions, None

    if candidate.status == "voided":
        exceptions.append(CheckExceptionType.VOIDED)

    # The issued check was already paid: whatever the amount now, this is a second presentment
    # (FULL_REVIEW_PLAN.md FR-D11). is_duplicate_paid only catches an equal amount.
    if candidate.status == "paid" and CheckExceptionType.DUPLICATE_PAID not in exceptions:
        exceptions.append(CheckExceptionType.DUPLICATE_PAID)

    if inputs.presented_amount != candidate.amount:
        exceptions.append(CheckExceptionType.AMOUNT_MISMATCH)

    if inputs.ocr_payee is not None:
        similarity = fuzz.token_set_ratio(normalize_payee(inputs.ocr_payee), normalize_payee(candidate.payee_name))
        if similarity < inputs.payee_fuzzy_threshold:
            exceptions.append(CheckExceptionType.PAYEE_MISMATCH)

    days_since_issue = (inputs.presented_date - candidate.issue_date).days
    if days_since_issue > inputs.stale_date_threshold_days:
        exceptions.append(CheckExceptionType.STALE_DATED)

    return exceptions, candidate.id
