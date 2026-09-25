# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import uuid

from openclear.networks.ach.types import AchExceptionType, AchMatchInputs, AuthorizationSnapshot


def _select_authorization(inputs: AchMatchInputs) -> AuthorizationSnapshot | None:
    """An exact receiver_id match takes precedence over a wildcard (receiver_id=None)
    rule for the same originator — lets an account holder set a blanket authorization for
    a company while tightening limits for one specific receiver within it."""
    exact_matches = [
        auth
        for auth in inputs.candidate_authorizations
        if auth.receiver_id is not None and auth.receiver_id == inputs.receiver_id
    ]
    if exact_matches:
        return exact_matches[0]

    wildcard_matches = [auth for auth in inputs.candidate_authorizations if auth.receiver_id is None]
    return wildcard_matches[0] if wildcard_matches else None


def evaluate_ach_rules(inputs: AchMatchInputs) -> tuple[list[AchExceptionType], uuid.UUID | None]:
    """Pure function, mirrors networks/check/rules.py's shape. Credits are never subject
    to positive-pay checks here — ACH positive pay concerns money leaving the account."""
    if not inputs.is_debit:
        return [], None

    if inputs.debit_block_all:
        return [AchExceptionType.UNAUTHORIZED_ORIGINATOR], None

    if not inputs.candidate_authorizations:
        return [AchExceptionType.UNAUTHORIZED_ORIGINATOR], None

    auth = _select_authorization(inputs)
    if auth is None:
        # The originator has authorization(s) on this account, just none covering this
        # specific receiver_id — a materially different signal than "unauthorized
        # originator" for a reviewer, so it gets its own exception type.
        return [AchExceptionType.RECEIVER_ID_NOT_PERMITTED], inputs.candidate_authorizations[0].id

    exceptions: list[AchExceptionType] = []

    if auth.max_amount is not None and inputs.amount > auth.max_amount:
        exceptions.append(AchExceptionType.AMOUNT_EXCEEDS_LIMIT)

    # debit_count_this_period counts PRIOR settled debits (not including this one) in the
    # trailing period — so >= means this transaction would be the one that tips it over.
    if auth.frequency_limit is not None and inputs.debit_count_this_period >= auth.frequency_limit:
        exceptions.append(AchExceptionType.FREQUENCY_EXCEEDED)

    if auth.allowed_sec_codes is not None and inputs.sec_code not in auth.allowed_sec_codes:
        exceptions.append(AchExceptionType.SEC_CODE_NOT_PERMITTED)

    return exceptions, auth.id
