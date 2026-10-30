# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Two people, or a person and the cut-off, acting on one item at the same moment, on Postgres
(FULL_REVIEW_PLAN.md FR1, R1 and R4). Each case holds the item's row lock in one session and
shows that the other waits for it (or, for the cut-off, skips it) and then acts on what the
first one did. Runs only with OPENCLEAR_TEST_DATABASE_URL set."""

import threading
import time
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from openclear.db.tenancy import bind_session_to_tenant
from openclear.domain.decision import Decision, DecisionOutcome
from openclear.domain.exception_item import ExceptionItem, ExceptionStatus
from openclear.repositories.exception_repo import ExceptionRepository
from openclear.services import decision_service
from openclear.services.decision_service import DecisionError
from tests.conftest import TEST_DATABASE_URL
from tests.test_decisions.test_changeable import _item
from tests.test_web.test_web_exception_review import _ctx

pytestmark = [pytest.mark.skipif(not TEST_DATABASE_URL, reason="needs OPENCLEAR_TEST_DATABASE_URL (Postgres)"), pytest.mark.concurrency]


class _Second:
    """Runs `act(session)` on another connection, in a thread, and keeps what it returned."""

    def __init__(self, factory, tenant_id, act):
        self.result, self.error = None, None
        self._factory, self._tenant_id, self._act = factory, tenant_id, act
        self._thread = threading.Thread(target=self._run)

    def _run(self) -> None:
        session = self._factory()
        try:
            bind_session_to_tenant(session, self._tenant_id)
            self.result = self._act(session)
            session.commit()
        except Exception as exc:  # noqa: BLE001 -- asserted on by the test
            self.error = exc
        finally:
            session.close()

    def start_and_confirm_waiting(self) -> "_Second":
        self._thread.start()
        time.sleep(0.5)
        assert self._thread.is_alive(), "the second session should be waiting for the item's lock"
        return self

    def join(self):
        self._thread.join(timeout=10)
        assert not self._thread.is_alive()
        if self.error is not None:
            raise self.error
        return self.result


def _sessions(engine):
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def _decide(session, tenant, item, user, outcome=DecisionOutcome.PAY):
    return decision_service.decide(session, tenant.id, item.id, _ctx(tenant, user), outcome=outcome, reason_code="Reviewed",
                                   notes=None, channel="web")


def test_a_recommendation_waits_for_a_decision_and_then_sees_it(engine, db_session, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="cc-rec-dec")
    item = _item(db_session, tenant, account, number="6101")
    factory = _sessions(engine)

    first = factory()
    bind_session_to_tenant(first, tenant.id)
    assert _decide(first, tenant, item, users["admin"]).error is None  # holds the item's lock, not committed
    second = _Second(factory, tenant.id, lambda s: decision_service.submit_recommendation(
        s, tenant.id, item.id, _ctx(tenant, users["preparer"]), outcome=DecisionOutcome.RETURN, reason_code="Looks wrong",
        notes=None)).start_and_confirm_waiting()
    first.commit()
    first.close()

    assert second.join().error == DecisionError.ALREADY_DECIDED
    db_session.expire_all()
    stored = db_session.get(ExceptionItem, item.id)
    assert stored.status == ExceptionStatus.PAY  # not left pending approval with a decision behind it
    assert stored.recommended_by_user_id is None


@pytest.mark.asvs("2.3.4")
def test_two_decisions_at_once_one_wins_and_the_other_is_told(engine, db_session, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="cc-dec-dec")
    item = _item(db_session, tenant, account, number="6102")
    factory = _sessions(engine)

    first = factory()
    bind_session_to_tenant(first, tenant.id)
    assert _decide(first, tenant, item, users["admin"]).error is None
    second = _Second(factory, tenant.id, lambda s: _decide(s, tenant, item, users["approver"], DecisionOutcome.RETURN)
                     ).start_and_confirm_waiting()
    first.commit()
    first.close()

    assert second.join().error == DecisionError.ALREADY_DECIDED
    with factory() as session:
        bind_session_to_tenant(session, tenant.id)
        decisions = session.execute(select(Decision).where(Decision.exception_item_id == item.id)).scalars().all()
    assert [d.outcome for d in decisions] == [DecisionOutcome.PAY]


def test_the_cutoff_skips_an_item_someone_is_taking_back(engine, db_session, tenant_factory):
    """Undo holds the item; the cut-off passes over it, so nothing goes out for a decision that's
    about to disappear."""
    tenant, account, users = tenant_factory.make(slug="cc-undo-lock")
    item = _item(db_session, tenant, account, number="6103", due_in=timedelta(seconds=2))
    factory = _sessions(engine)
    with factory() as session:
        bind_session_to_tenant(session, tenant.id)
        assert _decide(session, tenant, item, users["admin"]).error is None
        session.commit()
    time.sleep(2.5)  # now past its cut-off, but not yet locked

    first = factory()
    bind_session_to_tenant(first, tenant.id)
    # Taking it back, the moment the cut-off passes: the item's lock is held while the undo runs.
    held = ExceptionRepository(first, tenant.id).get_for_update(item.id)
    assert held is not None
    with factory() as sweeper:
        assert decision_service.lock_due(sweeper, now=datetime.now(timezone.utc)) == 0  # busy: skipped, not waited on
    first.rollback()
    first.close()

    with factory() as sweeper:  # the next run gets it
        assert decision_service.lock_due(sweeper, now=datetime.now(timezone.utc)) == 1


def test_a_change_waits_for_the_cutoff_and_is_then_refused(engine, db_session, tenant_factory):
    """The cut-off holds the item while it locks the decision; a change arriving then waits, and
    is refused once the decision is final."""
    tenant, account, users = tenant_factory.make(slug="cc-lock-change")
    item = _item(db_session, tenant, account, number="6104", due_in=timedelta(seconds=2))
    factory = _sessions(engine)
    with factory() as session:
        bind_session_to_tenant(session, tenant.id)
        assert _decide(session, tenant, item, users["admin"]).error is None
        session.commit()
    time.sleep(2.5)

    first = factory()
    bind_session_to_tenant(first, tenant.id)
    held = ExceptionRepository(first, tenant.id).get_for_update(item.id)
    decision = first.execute(select(Decision).where(Decision.exception_item_id == item.id)).scalar_one()
    decision_service.lock(first, decision, held)  # what lock_due does for this item, before its commit
    second = _Second(factory, tenant.id, lambda s: decision_service.undo_decision(s, tenant.id, item.id, _ctx(tenant, users["admin"]))
                     ).start_and_confirm_waiting()
    first.commit()
    first.close()

    assert second.join().error == DecisionError.LOCKED
    with factory() as session:
        bind_session_to_tenant(session, tenant.id)
        kept = session.execute(select(Decision).where(Decision.exception_item_id == item.id)).scalar_one()
    assert kept.locked_at is not None and kept.feed_sequence is not None
