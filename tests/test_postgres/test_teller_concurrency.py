# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""A branch, a file and the cut-off at the same moment, on Postgres (REVIEW_1_33_PLAN.md RV1):
a check cashed at a branch while its file presentment is in flight is paid once; one
verification cashed twice at once records one payment, and the loser is told which; *Pay all*
and the cut-off sweep over the same check leave one decision. As in
test_decision_state_concurrency.py, one session holds a lock and the other is shown to wait,
then to act on what the first did. Runs only with OPENCLEAR_TEST_DATABASE_URL set."""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from openclear.db.tenancy import bind_session_to_tenant
from openclear.domain.decision import Decision
from openclear.domain.exception_item import ExceptionItem, ExceptionStatus
from openclear.domain.paid_item import PaidItem, PaidItemMatchStatus, PaidItemSettlementStatus
from openclear.services import issued_item_service, teller_service
from openclear.services.teller_service import TellerError, VerifyRequest
from tests.conftest import TEST_DATABASE_URL
from tests.test_networks.check.test_reverse_positive_pay import _present, _reverse_customer
from tests.test_postgres.test_decision_state_concurrency import _Second, _decide, _sessions
from tests.test_postgres.test_presentment_outbox_change_concurrency import _present as _present_file

pytestmark = [pytest.mark.skipif(not TEST_DATABASE_URL, reason="needs OPENCLEAR_TEST_DATABASE_URL (Postgres)"), pytest.mark.concurrency]


def _verified(session, tenant, account, number):
    issued_item_service.create_issued_item(session, tenant.id, issued_item_service.IssuedItemInput(
        account_id=account.id, check_number=number, amount=Decimal("250.00"), payee_name="Acme", issue_date=date(2026, 10, 1)),
        submitted_by_user_id=None)
    session.commit()
    verification = teller_service.verify(session, tenant.id, VerifyRequest(
        account_number=account.account_number, check_number=number, amount=Decimal("250.00"), payee_name="Acme"), user_id=None)
    session.commit()
    assert verification.result == "match"
    return verification


def _cash(session, tenant, verification):
    return teller_service.cash(session, tenant.id, verification.id, Decimal("250.00"), user_id=None)


@pytest.mark.asvs("2.3.4")
def test_a_check_cashed_while_its_file_presentment_is_in_flight_is_paid_once(engine, db_session, tenant_factory):
    tenant, account, _users = tenant_factory.make(slug="cc-teller-file")
    verification = _verified(db_session, tenant, account, "7101")
    factory = _sessions(engine)

    first = factory()
    bind_session_to_tenant(first, tenant.id)
    paid = _present_file(first, tenant, account, "7101")  # the file's presentment holds the issued item's lock
    assert paid.match_status == PaidItemMatchStatus.MATCHED
    second = _Second(factory, tenant.id, lambda s: _cash(s, tenant, verification)).start_and_confirm_waiting()
    first.commit()
    first.close()

    cashed = second.join()
    assert cashed.exception_types == ["duplicate_paid"]  # the branch's presentment turned out to be the second
    with factory() as session:
        bind_session_to_tenant(session, tenant.id)
        statuses = session.execute(select(PaidItem.settlement_status).where(PaidItem.tenant_id == tenant.id, PaidItem.check_number == "7101")
                                   .order_by(PaidItem.created_at)).scalars().all()
        assert statuses == [PaidItemSettlementStatus.PAID, PaidItemSettlementStatus.PENDING]


@pytest.mark.asvs("2.3.4")
def test_one_verification_cashed_twice_at_once_records_one_payment(engine, db_session, tenant_factory):
    tenant, account, _users = tenant_factory.make(slug="cc-teller-twice")
    verification = _verified(db_session, tenant, account, "7102")
    factory = _sessions(engine)

    first = factory()
    bind_session_to_tenant(first, tenant.id)
    cashed = _cash(first, tenant, verification)  # claims the verification: holds its row
    second = _Second(factory, tenant.id, lambda s: _cash(s, tenant, verification)).start_and_confirm_waiting()
    first.commit()
    first.close()

    with pytest.raises(TellerError) as refused:
        second.join()
    assert refused.value.details["paid_item_id"] == cashed.paid_item.id  # told what the first call recorded
    with factory() as session:
        bind_session_to_tenant(session, tenant.id)
        assert session.execute(select(func.count()).select_from(PaidItem).where(PaidItem.tenant_id == tenant.id,
                                                                                 PaidItem.check_number == "7102")).scalar_one() == 1


def test_pay_all_and_the_cutoff_sweep_over_one_check_leave_one_decision(engine, monkeypatch, db_session, tenant_factory):
    from openclear.workers import tasks

    tenant, account, users = tenant_factory.make(slug="cc-teller-payall")
    _reverse_customer(db_session, tenant, account)
    item = _present(db_session, tenant, account, "9902")
    item.decision_deadline = datetime.now(timezone.utc) - timedelta(seconds=1)  # the cut-off has just passed
    db_session.commit()
    factory = _sessions(engine)
    monkeypatch.setattr(tasks, "get_session_factory", lambda: factory)

    person = factory()
    bind_session_to_tenant(person, tenant.id)
    assert _decide(person, tenant, item, users["admin"]).error is None  # Pay all, mid-way: holds the item's lock
    tasks.sweep_expired_dispositions_job()  # passes over it rather than paying it a second time
    person.commit()
    person.close()
    tasks.sweep_expired_dispositions_job()  # and finds it decided

    with factory() as session:
        bind_session_to_tenant(session, tenant.id)
        decisions = session.execute(select(Decision).where(Decision.exception_item_id == item.id)).scalars().all()
        assert len(decisions) == 1 and decisions[0].locked_at is not None  # the sweep locked the person's decision
        assert session.get(ExceptionItem, item.id).status == ExceptionStatus.PAY
        assert session.get(PaidItem, item.source_item_id).settlement_status == PaidItemSettlementStatus.PAID
