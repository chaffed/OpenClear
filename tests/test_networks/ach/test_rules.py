# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import uuid
from decimal import Decimal

from openclear.networks.ach.types import AchExceptionType, AchMatchInputs, AuthorizationSnapshot

WILDCARD_AUTH = AuthorizationSnapshot(
    id=uuid.uuid4(), receiver_id=None, max_amount=Decimal("500.00"), frequency_limit=3, allowed_sec_codes=["PPD", "WEB"]
)
EXACT_AUTH = AuthorizationSnapshot(
    id=uuid.uuid4(), receiver_id="EMP123", max_amount=Decimal("200.00"), frequency_limit=1, allowed_sec_codes=["PPD"]
)


def base_inputs(**overrides) -> AchMatchInputs:
    defaults = dict(
        is_debit=True,
        debit_block_all=False,
        amount=Decimal("100.00"),
        sec_code="PPD",
        receiver_id="EMP123",
        candidate_authorizations=[WILDCARD_AUTH],
        debit_count_this_period=0,
    )
    defaults.update(overrides)
    return AchMatchInputs(**defaults)


def test_credit_transactions_are_never_evaluated():
    from openclear.networks.ach.rules import evaluate_ach_rules

    exceptions, related_id = evaluate_ach_rules(base_inputs(is_debit=False, candidate_authorizations=[]))

    assert exceptions == []
    assert related_id is None


def test_clean_debit_within_limits_matches_wildcard_rule():
    from openclear.networks.ach.rules import evaluate_ach_rules

    exceptions, related_id = evaluate_ach_rules(base_inputs())

    assert exceptions == []
    assert related_id == WILDCARD_AUTH.id


def test_debit_block_all_rejects_regardless_of_authorization():
    from openclear.networks.ach.rules import evaluate_ach_rules

    exceptions, related_id = evaluate_ach_rules(base_inputs(debit_block_all=True))

    assert exceptions == [AchExceptionType.UNAUTHORIZED_ORIGINATOR]
    assert related_id is None


def test_no_candidate_authorizations_flags_unauthorized_originator():
    from openclear.networks.ach.rules import evaluate_ach_rules

    exceptions, related_id = evaluate_ach_rules(base_inputs(candidate_authorizations=[]))

    assert exceptions == [AchExceptionType.UNAUTHORIZED_ORIGINATOR]
    assert related_id is None


def test_receiver_id_not_covered_by_any_rule_is_a_distinct_exception():
    """Originator has an authorization, just not one covering this receiver_id — must be
    distinguishable from 'no authorization at all' for a reviewer."""
    from openclear.networks.ach.rules import evaluate_ach_rules

    scoped_auth = AuthorizationSnapshot(
        id=uuid.uuid4(), receiver_id="SOMEONE_ELSE", max_amount=None, frequency_limit=None, allowed_sec_codes=None
    )
    exceptions, related_id = evaluate_ach_rules(
        base_inputs(receiver_id="EMP123", candidate_authorizations=[scoped_auth])
    )

    assert exceptions == [AchExceptionType.RECEIVER_ID_NOT_PERMITTED]
    assert related_id == scoped_auth.id


def test_exact_receiver_match_takes_precedence_over_wildcard():
    from openclear.networks.ach.rules import evaluate_ach_rules

    exceptions, related_id = evaluate_ach_rules(
        base_inputs(
            receiver_id="EMP123",
            candidate_authorizations=[WILDCARD_AUTH, EXACT_AUTH],
            amount=Decimal("300.00"),  # exceeds EXACT_AUTH's 200 limit but not WILDCARD's 500
        )
    )

    assert AchExceptionType.AMOUNT_EXCEEDS_LIMIT in exceptions
    assert related_id == EXACT_AUTH.id


def test_wildcard_rule_matches_any_receiver_when_no_exact_rule_exists():
    from openclear.networks.ach.rules import evaluate_ach_rules

    exceptions, related_id = evaluate_ach_rules(base_inputs(receiver_id="ANY_RANDOM_RECEIVER"))

    assert exceptions == []
    assert related_id == WILDCARD_AUTH.id


def test_amount_exceeds_limit():
    from openclear.networks.ach.rules import evaluate_ach_rules

    exceptions, _ = evaluate_ach_rules(base_inputs(amount=Decimal("600.00")))

    assert AchExceptionType.AMOUNT_EXCEEDS_LIMIT in exceptions


def test_amount_at_exactly_the_limit_is_not_flagged():
    from openclear.networks.ach.rules import evaluate_ach_rules

    exceptions, _ = evaluate_ach_rules(base_inputs(amount=Decimal("500.00")))

    assert AchExceptionType.AMOUNT_EXCEEDS_LIMIT not in exceptions


def test_no_amount_limit_on_authorization_never_flags():
    from openclear.networks.ach.rules import evaluate_ach_rules

    unlimited = AuthorizationSnapshot(
        id=WILDCARD_AUTH.id, receiver_id=None, max_amount=None, frequency_limit=None, allowed_sec_codes=None
    )
    exceptions, _ = evaluate_ach_rules(
        base_inputs(amount=Decimal("999999.00"), candidate_authorizations=[unlimited])
    )

    assert exceptions == []


def test_frequency_exceeded_when_prior_count_meets_limit():
    from openclear.networks.ach.rules import evaluate_ach_rules

    exceptions, _ = evaluate_ach_rules(base_inputs(debit_count_this_period=3))

    assert AchExceptionType.FREQUENCY_EXCEEDED in exceptions


def test_frequency_not_exceeded_below_limit():
    from openclear.networks.ach.rules import evaluate_ach_rules

    exceptions, _ = evaluate_ach_rules(base_inputs(debit_count_this_period=2))

    assert AchExceptionType.FREQUENCY_EXCEEDED not in exceptions


def test_sec_code_not_permitted():
    from openclear.networks.ach.rules import evaluate_ach_rules

    exceptions, _ = evaluate_ach_rules(base_inputs(sec_code="CCD"))

    assert AchExceptionType.SEC_CODE_NOT_PERMITTED in exceptions


def test_sec_code_permitted_not_flagged():
    from openclear.networks.ach.rules import evaluate_ach_rules

    exceptions, _ = evaluate_ach_rules(base_inputs(sec_code="WEB"))

    assert AchExceptionType.SEC_CODE_NOT_PERMITTED not in exceptions


def test_multiple_violations_all_aggregate():
    from openclear.networks.ach.rules import evaluate_ach_rules

    exceptions, _ = evaluate_ach_rules(
        base_inputs(amount=Decimal("999.00"), sec_code="CCD", debit_count_this_period=5)
    )

    assert AchExceptionType.AMOUNT_EXCEEDS_LIMIT in exceptions
    assert AchExceptionType.SEC_CODE_NOT_PERMITTED in exceptions
    assert AchExceptionType.FREQUENCY_EXCEEDED in exceptions
