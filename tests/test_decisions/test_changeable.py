# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed
"""A decision can be changed until the cut-off (UI_PLAN.md UI-D13, the product owner's decision):
until then nothing leaves OpenClear; at the cut-off it locks and goes out. A change follows the
rules a first decision does, and every earlier version is kept."""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select

from openclear.domain.decision import Decision, DecisionOutcome
from openclear.domain.exception_item import ExceptionStatus
from openclear.networks.check.ingestion import PaidItemSubmission, ingest_paid_item
from openclear.repositories.exception_repo import ExceptionRepository
from openclear.services import decision_service
from openclear.services.decision_service import DecisionError
from tests.test_web.test_web_exception_review import _ctx


def _item(db_session, tenant, account, number="5001", due_in=timedelta(hours=3)):
    paid = ingest_paid_item(db_session, tenant.id, PaidItemSubmission(account_id=account.id, check_number=number,
                                                                      presented_amount=Decimal("40.00"), presented_date=date(2026, 10, 1)))
    item = ExceptionRepository(db_session, tenant.id).list(source_item_id=paid.id)[0]
    item.decision_deadline = datetime.now(timezone.utc) + due_in if due_in is not None else None
    db_session.commit()
    return item


def _decide(db_session, tenant, item, user, outcome=DecisionOutcome.PAY, reason="Reviewed"):
    result = decision_service.decide(db_session, tenant.id, item.id, _ctx(tenant, user), outcome=outcome, reason_code=reason, notes=None,
                                     channel="web")
    db_session.commit()
    return result


def test_before_the_cutoff_a_decision_is_recorded_but_not_sent(db_session, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="chg-held")
    item = _item(db_session, tenant, account)
    result = _decide(db_session, tenant, item, users["admin"])
    assert result.error is None and item.status == ExceptionStatus.PAY
    assert result.decision.locked_at is None and result.decision.feed_sequence is None  # not in the feed yet
    assert decision_service.changeable_until(item) is not None


def test_with_no_cutoff_ahead_a_decision_is_final_at_once(db_session, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="chg-final")
    item = _item(db_session, tenant, account, due_in=None)
    result = _decide(db_session, tenant, item, users["admin"])
    assert result.decision.locked_at is not None and result.decision.feed_sequence == 1
    changed = decision_service.change_decision(db_session, tenant.id, item.id, _ctx(tenant, users["admin"]),
                                               outcome=DecisionOutcome.RETURN, reason_code="Stop payment", notes=None)
    assert changed.error == DecisionError.LOCKED


def test_a_change_keeps_the_earlier_version(db_session, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="chg-change")
    item = _item(db_session, tenant, account)
    _decide(db_session, tenant, item, users["admin"])
    changed = decision_service.change_decision(db_session, tenant.id, item.id, _ctx(tenant, users["approver"]),
                                               outcome=DecisionOutcome.RETURN, reason_code="Stop payment", notes="Customer called")
    db_session.commit()
    assert changed.error is None and item.status == ExceptionStatus.RETURN
    assert (changed.decision.outcome, changed.decision.decided_by_user_id) == (DecisionOutcome.RETURN, users["approver"].id)
    [earlier] = decision_service.revisions(db_session, tenant.id, item.id)
    assert (earlier.outcome, earlier.decided_by_user_id, earlier.revised_by_user_id, earlier.undone) == (
        DecisionOutcome.PAY, users["admin"].id, users["approver"].id, False)
    assert db_session.execute(select(Decision).where(Decision.exception_item_id == item.id)).scalars().all() == [changed.decision]


def test_undo_opens_the_item_again(db_session, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="chg-undo")
    item = _item(db_session, tenant, account)
    _decide(db_session, tenant, item, users["admin"])
    undone = decision_service.undo_decision(db_session, tenant.id, item.id, _ctx(tenant, users["admin"]))
    db_session.commit()
    assert undone.error is None and item.status == ExceptionStatus.OPEN
    assert decision_service.get_decision_for_exception(db_session, tenant.id, item.id) is None
    assert [r.undone for r in decision_service.revisions(db_session, tenant.id, item.id)] == [True]
    assert _decide(db_session, tenant, item, users["admin"], DecisionOutcome.RETURN, "Altered").error is None  # decide it again


def test_at_the_cutoff_it_locks_and_goes_out(db_session, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="chg-lock")
    item = _item(db_session, tenant, account)
    decision = _decide(db_session, tenant, item, users["admin"]).decision
    assert decision_service.lock_due(db_session) == 0  # not yet
    item.decision_deadline = datetime.now(timezone.utc) - timedelta(minutes=1)
    db_session.commit()
    assert decision_service.lock_due(db_session) == 1
    db_session.commit()
    assert decision.locked_at is not None and decision.feed_sequence == 1
    refused = decision_service.undo_decision(db_session, tenant.id, item.id, _ctx(tenant, users["admin"]))
    assert refused.error == DecisionError.LOCKED


def test_under_dual_control_a_change_waits_for_someone_else(db_session, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="chg-dual", require_dual_control=True)
    item = _item(db_session, tenant, account)
    decision_service.submit_recommendation(db_session, tenant.id, item.id, _ctx(tenant, users["preparer"]),
                                           outcome=DecisionOutcome.PAY, reason_code="Looks right", notes=None)
    _decide(db_session, tenant, item, users["approver"])
    preparer, approver = _ctx(tenant, users["preparer"]), _ctx(tenant, users["approver"])

    # Changing outright isn't allowed, and neither is undoing: a change is recommended.
    assert decision_service.change_decision(db_session, tenant.id, item.id, approver, outcome=DecisionOutcome.RETURN,
                                            reason_code="Stop payment", notes=None).error == DecisionError.CHANGE_NEEDS_APPROVAL
    assert decision_service.undo_decision(db_session, tenant.id, item.id, approver).error == DecisionError.CHANGE_NEEDS_APPROVAL
    assert decision_service.recommend_change(db_session, tenant.id, item.id, preparer, outcome=DecisionOutcome.RETURN,
                                             reason_code="Stop payment", notes=None).error is None
    db_session.commit()
    # The decision in place stands until it's approved, by someone else.
    assert item.status == ExceptionStatus.PAY and item.change_recommended_at is not None
    assert decision_service.change_decision(db_session, tenant.id, item.id, preparer, outcome=DecisionOutcome.RETURN,
                                            reason_code="Stop payment", notes=None).error == DecisionError.MAKER_CANNOT_APPROVE_OWN_RECOMMENDATION
    approved = decision_service.change_decision(db_session, tenant.id, item.id, approver, outcome=DecisionOutcome.RETURN,
                                                reason_code="Stop payment", notes=None)
    db_session.commit()
    assert approved.error is None and item.status == ExceptionStatus.RETURN and item.change_recommended_at is None
    assert approved.decision.submitted_by_user_id == users["preparer"].id


def test_a_change_nobody_approves_by_the_cutoff_is_dropped(db_session, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="chg-drop", require_dual_control=True)
    item = _item(db_session, tenant, account)
    decision_service.submit_recommendation(db_session, tenant.id, item.id, _ctx(tenant, users["preparer"]),
                                           outcome=DecisionOutcome.PAY, reason_code="Looks right", notes=None)
    decision = _decide(db_session, tenant, item, users["approver"]).decision
    decision_service.recommend_change(db_session, tenant.id, item.id, _ctx(tenant, users["preparer"]), outcome=DecisionOutcome.RETURN,
                                      reason_code="Stop payment", notes=None)
    item.decision_deadline = datetime.now(timezone.utc) - timedelta(minutes=1)
    db_session.commit()
    decision_service.lock_due(db_session)
    db_session.commit()
    assert decision.outcome == DecisionOutcome.PAY and decision.locked_at is not None
    assert item.change_recommended_at is None and item.recommended_outcome == "pay"


def test_at_the_cutoff_one_failure_doesnt_hold_the_others(db_session, tenant_factory, monkeypatch):
    """Each decision locks in its own transaction (FULL_REVIEW_PLAN.md FR-D5): one that fails is
    tried again next run, and the rest still go out now."""
    tenant, account, users = tenant_factory.make(slug="chg-lock-each")
    first, second = _item(db_session, tenant, account, number="5101"), _item(db_session, tenant, account, number="5102")
    for item in (first, second):
        _decide(db_session, tenant, item, users["admin"])
        item.decision_deadline = datetime.now(timezone.utc) - timedelta(minutes=1)
    db_session.commit()
    real_lock = decision_service.lock

    def failing_for_first(session, decision, exception):
        if exception.id == first.id:
            raise RuntimeError("core unreachable")
        real_lock(session, decision, exception)

    monkeypatch.setattr(decision_service, "lock", failing_for_first)
    assert decision_service.lock_due(db_session) == 1
    monkeypatch.setattr(decision_service, "lock", real_lock)
    assert decision_service.lock_due(db_session) == 1  # the next run gets the first
    locked = db_session.execute(select(Decision).where(Decision.exception_item_id.in_([first.id, second.id]))).scalars().all()
    assert all(d.locked_at is not None for d in locked) and len(locked) == 2


def test_the_lock_job_runs_every_minute_with_the_sweep(monkeypatch):
    from openclear.config import get_settings
    from openclear.workers import scheduler

    monkeypatch.setenv("OPENCLEAR_ENABLE_DISPOSITION_SCHEDULER", "true")
    get_settings.cache_clear()
    assert scheduler.expected_jobs()["decision_lock_job"] == timedelta(seconds=60)
