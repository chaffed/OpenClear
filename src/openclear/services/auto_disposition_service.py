# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Default disposition for exceptions whose decision_deadline passes with no human
decision — a per-(customer, network) CustomerDispositionSetting picks what happens: leave
it open (NONE, the default), always pay/return (FIXED_*), or let the ML model decide
(ML_DETERMINED). compute_decision_deadline() is called from the check/ACH ingestion paths
to actually populate ExceptionItem.decision_deadline (otherwise dead ever since that column
was added); auto_decide_exception() is the "decide this one expired exception" entry point
workers/tasks.py::sweep_expired_dispositions_job() calls per row. Both intentionally return
None/skip rather than guess whenever the inputs needed for a real decision aren't
available (no trained model yet, no configured ACH return reason) — see each function's
docstring."""

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from openclear.config import get_settings
from openclear.domain.ach_transaction import AchSettlementStatus, AchTransaction
from openclear.domain.customer_disposition_setting import CustomerDispositionSetting, DispositionMode
from openclear.domain.decision import Decision, DecisionOutcome, DecisionSource
from openclear.domain.exception_item import ExceptionItem, ExceptionStatus
from openclear.ml.predict import score_exception
from openclear.networks.registry import get_adapter, registered_codes
from openclear.repositories.ach_return_reason_repo import AchReturnReasonRepository
from openclear.repositories.customer_disposition_setting_repo import CustomerDispositionSettingRepository
from openclear.repositories.decision_repo import DecisionRepository
from openclear.services import decision_feed_service, notification_service


@dataclass(frozen=True, slots=True)
class CustomerNetworkDispositionSummary:
    """One row per registered network for a given customer — what the "Default
    disposition" table on the per-customer admin page is built from."""

    network_code: str
    mode: DispositionMode
    response_window_hours: int
    default_ach_return_reason_id: uuid.UUID | None

_ML_PAY_THRESHOLD = 0.5


def _get_setting(
    session: Session, tenant_id: uuid.UUID, customer_id: uuid.UUID, network_code: str
) -> CustomerDispositionSetting | None:
    settings = CustomerDispositionSettingRepository(session, tenant_id).list(customer_id=customer_id, network_code=network_code)
    return settings[0] if settings else None


def get_disposition_mode(session: Session, tenant_id: uuid.UUID, customer_id: uuid.UUID, network_code: str) -> DispositionMode:
    """Absence of a row means NONE — see CustomerDispositionSetting's docstring."""
    setting = _get_setting(session, tenant_id, customer_id, network_code)
    return setting.mode if setting else DispositionMode.NONE


def get_response_window_hours(session: Session, tenant_id: uuid.UUID, customer_id: uuid.UUID, network_code: str) -> int:
    setting = _get_setting(session, tenant_id, customer_id, network_code)
    if setting is not None and setting.response_window_hours is not None:
        return setting.response_window_hours
    return get_settings().default_disposition_response_window_hours


def set_disposition_setting(
    session: Session,
    tenant_id: uuid.UUID,
    customer_id: uuid.UUID,
    network_code: str,
    *,
    mode: DispositionMode,
    response_window_hours: int | None,
    default_ach_return_reason_id: uuid.UUID | None,
) -> CustomerDispositionSetting:
    repo = CustomerDispositionSettingRepository(session, tenant_id)
    setting = _get_setting(session, tenant_id, customer_id, network_code)
    if setting is not None:
        setting.mode = mode
        setting.response_window_hours = response_window_hours
        setting.default_ach_return_reason_id = default_ach_return_reason_id
    else:
        setting = CustomerDispositionSetting(
            customer_id=customer_id,
            network_code=network_code,
            mode=mode,
            response_window_hours=response_window_hours,
            default_ach_return_reason_id=default_ach_return_reason_id,
        )
        repo.add(setting)
    session.flush()
    return setting


def get_disposition_summary(
    session: Session, tenant_id: uuid.UUID, customer_id: uuid.UUID
) -> list[CustomerNetworkDispositionSummary]:
    """One entry per registered payment network — what the per-customer admin page's
    "Default disposition" card is built from, same fan-out shape as
    customer_ml_service.get_ml_summary()."""
    summaries = []
    for network_code in registered_codes():
        setting = _get_setting(session, tenant_id, customer_id, network_code)
        summaries.append(
            CustomerNetworkDispositionSummary(
                network_code=network_code,
                mode=setting.mode if setting else DispositionMode.NONE,
                response_window_hours=get_response_window_hours(session, tenant_id, customer_id, network_code),
                default_ach_return_reason_id=setting.default_ach_return_reason_id if setting else None,
            )
        )
    return summaries


def compute_decision_deadline(
    session: Session, tenant_id: uuid.UUID, customer_id: uuid.UUID | None, network_code: str
) -> datetime | None:
    """None for a tenant-wide/bank-staff-submitted item (no customer to look a setting up
    for — same "doesn't apply" rule ml/predict.py::_resolve_scoring_source uses for
    customer_id is None) or when the resolved mode is NONE. Otherwise now + the
    customer's configured (or global default) response window."""
    if customer_id is None:
        return None
    mode = get_disposition_mode(session, tenant_id, customer_id, network_code)
    if mode == DispositionMode.NONE:
        return None
    hours = get_response_window_hours(session, tenant_id, customer_id, network_code)
    return datetime.now(timezone.utc) + timedelta(hours=hours)


def auto_decide_exception(session: Session, exception_item: ExceptionItem) -> Decision | None:
    """Decides one expired exception per its customer's configured disposition mode.
    Returns None (leaving the exception untouched, to be retried on the next sweep) when:
    the resolved mode is NONE (shouldn't reach here via the sweep's own query, but guarded
    since this is also callable directly); ML_DETERMINED and no model/score is available
    yet; or the outcome resolves to an ACH RETURN with no default_ach_return_reason_id
    configured. Mirrors decision_service.decide()'s mechanics (build_features, create the
    Decision, flip exception status, flip an ACH RETURN's underlying settlement_status) but
    with no maker-checker check (there's no maker) and no human decider."""
    if exception_item.customer_id is None:
        return None
    mode = get_disposition_mode(session, exception_item.tenant_id, exception_item.customer_id, exception_item.network_code)

    if mode == DispositionMode.NONE:
        return None
    if mode == DispositionMode.FIXED_PAY:
        outcome = DecisionOutcome.PAY
        source = DecisionSource.AUTO_DEFAULT
        hours = get_response_window_hours(session, exception_item.tenant_id, exception_item.customer_id, exception_item.network_code)
        reason_code = f"Auto-decided: no response within {hours}h window"
    elif mode == DispositionMode.FIXED_RETURN:
        outcome = DecisionOutcome.RETURN
        source = DecisionSource.AUTO_DEFAULT
        hours = get_response_window_hours(session, exception_item.tenant_id, exception_item.customer_id, exception_item.network_code)
        reason_code = f"Auto-decided: no response within {hours}h window"
    else:  # ML_DETERMINED
        score = score_exception(session, exception_item)
        if score is None:
            return None
        outcome = DecisionOutcome.PAY if score >= _ML_PAY_THRESHOLD else DecisionOutcome.RETURN
        source = DecisionSource.AUTO_ML
        reason_code = f"Auto-decided by ML (score={score:.3f})"

    return_transaction_code: str | None = None
    if outcome == DecisionOutcome.RETURN and exception_item.network_code == "ach":
        setting = _get_setting(session, exception_item.tenant_id, exception_item.customer_id, exception_item.network_code)
        reason_id = setting.default_ach_return_reason_id if setting else None
        if reason_id is None:
            return None
        ach_return_reason = AchReturnReasonRepository(session, exception_item.tenant_id).get(reason_id)
        if ach_return_reason is None or not ach_return_reason.is_active:
            return None
        reason_code = ach_return_reason.reason_text
        return_transaction_code = ach_return_reason.transaction_code

    adapter = get_adapter(exception_item.network_code)
    features = adapter.build_features(session, exception_item)

    decision = Decision(
        exception_item_id=exception_item.id,
        outcome=outcome,
        reason_code=reason_code,
        return_transaction_code=return_transaction_code,
        notes=None,
        submitted_by_user_id=None,
        decided_by_user_id=None,
        source=source,
        features_json=features,
    )
    DecisionRepository(session, exception_item.tenant_id).add(decision)
    decision_feed_service.assign_sequence(session, decision, exception_item)

    exception_item.status = ExceptionStatus.PAY if outcome == DecisionOutcome.PAY else ExceptionStatus.RETURN

    if outcome == DecisionOutcome.RETURN and exception_item.network_code == "ach":
        source_item = adapter.load_source_item(session, exception_item.source_item_id)
        if isinstance(source_item, AchTransaction):
            source_item.settlement_status = AchSettlementStatus.RETURNED

    session.flush()
    notification_service.notify_exception_auto_decided(session, exception_item, decision)
    return decision
