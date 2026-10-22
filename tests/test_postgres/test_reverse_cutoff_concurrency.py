# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Reverse positive pay at the cut-off, on Postgres (REVERSE_POSITIVE_PAY_PLAN.md RPP2): a
customer returning a check to review at the very moment its cut-off passes. The person holds
the item, the sweep passes over it rather than paying it by silence, and the return stands.
Runs only with OPENCLEAR_TEST_DATABASE_URL set."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from openclear.db.tenancy import bind_session_to_tenant
from openclear.domain.decision import Decision, DecisionOutcome
from openclear.domain.exception_item import ExceptionItem, ExceptionStatus
from openclear.repositories.exception_repo import ExceptionRepository
from openclear.services import decision_service
from tests.conftest import TEST_DATABASE_URL
from tests.test_networks.check.test_reverse_positive_pay import _present, _reverse_customer
from tests.test_web.test_web_exception_review import _ctx

pytestmark = [pytest.mark.skipif(not TEST_DATABASE_URL, reason="needs OPENCLEAR_TEST_DATABASE_URL (Postgres)"), pytest.mark.concurrency]


def test_a_return_at_the_cutoff_isnt_paid_over(engine, monkeypatch, db_session, tenant_factory):
    from openclear.services import check_return_reason_service
    from openclear.workers import tasks

    tenant, account, users = tenant_factory.make(slug="rpp-cc-cutoff")
    _reverse_customer(db_session, tenant, account)
    item = _present(db_session, tenant, account, "9901")
    item.decision_deadline = datetime.now(timezone.utc) - timedelta(seconds=1)  # the cut-off has just passed
    db_session.commit()
    reason = check_return_reason_service.list_check_return_reasons(db_session, tenant.id)[0]
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    person = factory()
    bind_session_to_tenant(person, tenant.id)
    held = ExceptionRepository(person, tenant.id).get_for_update(item.id)  # deciding: the item's lock is held
    assert held is not None
    monkeypatch.setattr(tasks, "get_session_factory", lambda: factory)
    tasks.sweep_expired_dispositions_job()  # passes over it, rather than waiting or paying it
    result = decision_service.decide(person, tenant.id, item.id, _ctx(tenant, users["admin"]), outcome=DecisionOutcome.RETURN,
                                     reason_code=reason.reason_text, notes=None, check_return_reason_id=reason.id, channel="web")
    assert result.error is None
    person.commit()
    person.close()

    with factory() as session:
        bind_session_to_tenant(session, tenant.id)
        decisions = session.execute(select(Decision).where(Decision.exception_item_id == item.id)).scalars().all()
        assert [d.outcome for d in decisions] == [DecisionOutcome.RETURN]
        assert session.get(ExceptionItem, item.id).status == ExceptionStatus.RETURN
