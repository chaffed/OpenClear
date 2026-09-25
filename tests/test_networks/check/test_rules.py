# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import uuid
from datetime import date
from decimal import Decimal

import pytest

from openclear.networks.check.types import CheckExceptionType, CheckMatchInputs, IssuedItemSnapshot

ISSUED = IssuedItemSnapshot(
    id=uuid.uuid4(),
    amount=Decimal("100.00"),
    payee_name="ACME SUPPLY CO",
    issue_date=date(2026, 1, 1),
    status="outstanding",
)


def base_inputs(**overrides) -> CheckMatchInputs:
    defaults = dict(
        presented_amount=Decimal("100.00"),
        presented_date=date(2026, 1, 10),
        is_duplicate_paid=False,
        active_stop_found=False,
        candidate_issued_item=ISSUED,
        stale_date_threshold_days=180,
        payee_fuzzy_threshold=85.0,
        ocr_payee=None,
        ocr_confidence=None,
    )
    defaults.update(overrides)
    return CheckMatchInputs(**defaults)


def test_clean_match_produces_no_exceptions():
    from openclear.networks.check.rules import evaluate_check_rules

    exceptions, related_id = evaluate_check_rules(base_inputs())

    assert exceptions == []
    assert related_id == ISSUED.id


def test_duplicate_paid_does_not_short_circuit_other_checks():
    from openclear.networks.check.rules import evaluate_check_rules

    exceptions, _ = evaluate_check_rules(
        base_inputs(is_duplicate_paid=True, presented_amount=Decimal("999.00"))
    )

    assert CheckExceptionType.DUPLICATE_PAID in exceptions
    assert CheckExceptionType.AMOUNT_MISMATCH in exceptions


def test_stopped_short_circuits_further_checks():
    from openclear.networks.check.rules import evaluate_check_rules

    exceptions, related_id = evaluate_check_rules(
        base_inputs(active_stop_found=True, presented_amount=Decimal("999.00"))
    )

    assert exceptions == [CheckExceptionType.STOPPED]
    assert related_id == ISSUED.id


def test_voided_candidate_flags_voided():
    from openclear.networks.check.rules import evaluate_check_rules

    voided = IssuedItemSnapshot(
        id=ISSUED.id, amount=ISSUED.amount, payee_name=ISSUED.payee_name, issue_date=ISSUED.issue_date, status="voided"
    )
    exceptions, _ = evaluate_check_rules(base_inputs(candidate_issued_item=voided))

    assert CheckExceptionType.VOIDED in exceptions


def test_not_in_file_short_circuits_when_no_candidate():
    from openclear.networks.check.rules import evaluate_check_rules

    exceptions, related_id = evaluate_check_rules(base_inputs(candidate_issued_item=None))

    assert exceptions == [CheckExceptionType.NOT_IN_FILE]
    assert related_id is None


def test_amount_mismatch_is_exact_decimal_no_tolerance():
    from openclear.networks.check.rules import evaluate_check_rules

    exceptions, _ = evaluate_check_rules(base_inputs(presented_amount=Decimal("100.01")))

    assert CheckExceptionType.AMOUNT_MISMATCH in exceptions


def test_amount_exact_match_does_not_flag_mismatch():
    from openclear.networks.check.rules import evaluate_check_rules

    exceptions, _ = evaluate_check_rules(base_inputs(presented_amount=Decimal("100.00")))

    assert CheckExceptionType.AMOUNT_MISMATCH not in exceptions


def test_payee_mismatch_flagged_below_fuzzy_threshold():
    from openclear.networks.check.rules import evaluate_check_rules

    exceptions, _ = evaluate_check_rules(base_inputs(ocr_payee="TOTALLY DIFFERENT ENTITY LLC"))

    assert CheckExceptionType.PAYEE_MISMATCH in exceptions


def test_payee_close_match_not_flagged():
    from openclear.networks.check.rules import evaluate_check_rules

    exceptions, _ = evaluate_check_rules(base_inputs(ocr_payee="Acme Supply Co."))

    assert CheckExceptionType.PAYEE_MISMATCH not in exceptions


def test_payee_check_skipped_when_no_ocr_data():
    from openclear.networks.check.rules import evaluate_check_rules

    exceptions, _ = evaluate_check_rules(base_inputs(ocr_payee=None))

    assert CheckExceptionType.PAYEE_MISMATCH not in exceptions


def test_stale_dated_beyond_threshold():
    from openclear.networks.check.rules import evaluate_check_rules

    exceptions, _ = evaluate_check_rules(
        base_inputs(presented_date=date(2026, 8, 1), stale_date_threshold_days=180)
    )

    assert CheckExceptionType.STALE_DATED in exceptions


def test_stale_dated_within_threshold_not_flagged():
    from openclear.networks.check.rules import evaluate_check_rules

    exceptions, _ = evaluate_check_rules(
        base_inputs(presented_date=date(2026, 6, 1), stale_date_threshold_days=180)
    )

    assert CheckExceptionType.STALE_DATED not in exceptions


def test_multiple_exceptions_aggregate_on_one_result():
    from openclear.networks.check.rules import evaluate_check_rules

    exceptions, _ = evaluate_check_rules(
        base_inputs(presented_amount=Decimal("50.00"), presented_date=date(2026, 12, 1))
    )

    assert CheckExceptionType.AMOUNT_MISMATCH in exceptions
    assert CheckExceptionType.STALE_DATED in exceptions


@pytest.mark.parametrize(
    "presented,issued",
    [
        (Decimal("100.00"), Decimal("100.00")),
        (Decimal("0.01"), Decimal("0.01")),
        (Decimal("100000.00"), Decimal("100000.00")),
    ],
)
def test_various_exact_amounts_match_cleanly(presented, issued):
    from openclear.networks.check.rules import evaluate_check_rules

    candidate = IssuedItemSnapshot(
        id=ISSUED.id, amount=issued, payee_name=ISSUED.payee_name, issue_date=ISSUED.issue_date, status="outstanding"
    )
    exceptions, _ = evaluate_check_rules(base_inputs(presented_amount=presented, candidate_issued_item=candidate))

    assert CheckExceptionType.AMOUNT_MISMATCH not in exceptions
