# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Presentments, decision files and change requests at the same moment, on Postgres
(FULL_REVIEW_PLAN.md FR2: R2, R3, R6). As in test_decision_state_concurrency.py, one session holds
the lock and the other is shown to wait, then to act on what the first did. Runs only with
OPENCLEAR_TEST_DATABASE_URL set."""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

from openclear.db.tenancy import bind_session_to_tenant
from openclear.domain.decision_outbox import DecisionBatch
from openclear.domain.issued_item import IssuedItemStatus
from openclear.domain.paid_item import PaidItemMatchStatus
from openclear.networks.check.ingestion import PaidItemSubmission, ingest_paid_item
from openclear.services import change_request_service as changes
from openclear.services import decision_outbox_service, issued_item_service
from openclear.services.change_kinds import ChangeError
from tests.conftest import TEST_DATABASE_URL
from tests.test_auth.test_dual_control import _policy, _second_admin
from tests.test_decisions.test_changeable import _decide, _item
from tests.test_postgres.test_decision_state_concurrency import _Second, _sessions

pytestmark = pytest.mark.skipif(not TEST_DATABASE_URL, reason="needs OPENCLEAR_TEST_DATABASE_URL (Postgres)")


def _present(session, tenant, account, number="7001", amount="250.00"):
    return ingest_paid_item(session, tenant.id, PaidItemSubmission(account_id=account.id, check_number=number,
                                                                   presented_amount=Decimal(amount), presented_date=date(2026, 10, 20)))


def test_a_check_presented_twice_at_once_is_paid_once(engine, db_session, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="cc-present")
    issued_item_service.create_issued_item(db_session, tenant.id, issued_item_service.IssuedItemInput(
        account_id=account.id, check_number="7001", amount=Decimal("250.00"), payee_name="Acme", issue_date=date(2026, 10, 1)),
        submitted_by_user_id=users["admin"].id)
    db_session.commit()
    factory = _sessions(engine)

    first = factory()
    bind_session_to_tenant(first, tenant.id)
    paid = _present(first, tenant, account)  # holds the issued item's lock
    assert paid.match_status == PaidItemMatchStatus.MATCHED
    second = _Second(factory, tenant.id, lambda s: _present(s, tenant, account)).start_and_confirm_waiting()
    first.commit()
    first.close()

    again = second.join()
    assert again.match_status == PaidItemMatchStatus.EXCEPTION  # caught as a duplicate, not paid again
    with factory() as session:
        bind_session_to_tenant(session, tenant.id)
        from openclear.domain.exception_item import ExceptionItem

        raised = session.execute(select(ExceptionItem.exception_types).where(ExceptionItem.source_item_id == again.id)).scalar_one()
        issued = issued_item_service.list_issued_items(session, tenant.id)[0]
    assert "duplicate_paid" in raised
    assert issued.status == IssuedItemStatus.PAID


def test_two_outbox_runs_at_once_write_one_file(engine, db_session, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="cc-outbox")
    item = _item(db_session, tenant, account, number="7101", due_in=None)
    assert _decide(db_session, tenant, item, users["admin"]).decision.feed_sequence == 1  # final at once: no cut-off ahead
    factory = _sessions(engine)

    first = factory()
    bind_session_to_tenant(first, tenant.id)
    assert decision_outbox_service.run(first, tenant.id) is not None  # holds the bank's outbox lock
    second = _Second(factory, tenant.id, lambda s: decision_outbox_service.run(s, tenant.id)).start_and_confirm_waiting()
    first.commit()
    first.close()

    assert second.join() is None  # nothing new once the first file is written
    with factory() as session:
        bind_session_to_tenant(session, tenant.id)
        assert len(session.execute(select(DecisionBatch).where(DecisionBatch.tenant_id == tenant.id)).scalars().all()) == 1


def test_two_approvals_at_once_apply_the_change_once(engine, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="cc-change")
    second_admin, third_admin = _second_admin(db_session, tenant), _second_admin(db_session, tenant, name="third")
    changes.request_or_apply(db_session, "password_policy", _policy(), tenant_id=tenant.id, user_id=users["admin"].id)
    waiting = changes.request_or_apply(db_session, "password_policy", _policy(password_min_length=8), tenant_id=tenant.id,
                                       user_id=users["admin"].id)
    db_session.commit()
    factory = _sessions(engine)

    first = factory()
    bind_session_to_tenant(first, tenant.id)
    changes.decide(first, tenant.id, waiting.change.id, approve=True, user_id=second_admin.id)  # holds the request
    second = _Second(factory, tenant.id, lambda s: changes.decide(s, tenant.id, waiting.change.id, approve=True,
                                                                  user_id=third_admin.id)).start_and_confirm_waiting()
    first.commit()
    first.close()

    with pytest.raises(ChangeError, match="isn't waiting"):
        second.join()
