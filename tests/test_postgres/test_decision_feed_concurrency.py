# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Decisions made at the same moment, on Postgres: each gets its own number, and none is
skipped (SDK_PLAN.md P0; services/decision_feed_service.py). Runs only with
OPENCLEAR_TEST_DATABASE_URL set."""

import threading

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from openclear.domain.decision import Decision, DecisionOutcome
from tests.conftest import TEST_DATABASE_URL
from tests.test_ml.test_per_customer_ml import _decide, _make_exception

pytestmark = pytest.mark.skipif(not TEST_DATABASE_URL, reason="needs OPENCLEAR_TEST_DATABASE_URL (Postgres)")

_DECIDERS = 8


def test_concurrent_decisions_get_consecutive_numbers(engine, db_session, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="feed-concurrent")
    exceptions = [_make_exception(db_session, tenant, account, users, f"C{n}", "1.00", "2.00") for n in range(_DECIDERS)]
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    start = threading.Barrier(_DECIDERS)
    errors = []

    def decide(exception) -> None:
        session = factory()
        try:
            start.wait()
            result = _decide(session, tenant, users, exception, DecisionOutcome.PAY)
            assert result.error is None, result.error
        except Exception as exc:  # noqa: BLE001 -- collected and asserted on below
            errors.append(exc)
        finally:
            session.close()

    threads = [threading.Thread(target=decide, args=(e,)) for e in exceptions]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    with factory() as session:
        numbers = sorted(session.execute(select(Decision.feed_sequence).where(Decision.tenant_id == tenant.id)).scalars())
    assert numbers == list(range(1, _DECIDERS + 1))
