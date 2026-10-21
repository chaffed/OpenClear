# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Real concurrent writers to one bank's audit log, on Postgres (the in-memory SQLite
used by the rest of the suite has one shared connection, so it can't run writers in
parallel). Runs only with OPENCLEAR_TEST_DATABASE_URL set."""

import threading
import uuid

import pytest
from sqlalchemy.orm import sessionmaker

from openclear.services import audit_log_service
from tests.conftest import TEST_DATABASE_URL

pytestmark = [pytest.mark.skipif(not TEST_DATABASE_URL, reason="needs OPENCLEAR_TEST_DATABASE_URL (Postgres)"), pytest.mark.concurrency]

_WRITERS = 8
_ENTRIES_EACH = 5


def test_concurrent_writers_never_fork_the_chain(engine, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="audit-concurrent")
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    start = threading.Barrier(_WRITERS)
    errors = []

    def writer(number: int) -> None:
        session = factory()
        try:
            start.wait()
            for n in range(_ENTRIES_EACH):
                audit_log_service.record_action(
                    session, tenant.id, actor_user_id=users["admin"].id, channel="api", action="test.concurrent",
                    summary=f"writer {number} entry {n}", resource_type="tenant", resource_id=uuid.uuid4(),
                )
                session.commit()
        except Exception as exc:  # noqa: BLE001 -- collected and asserted on below
            errors.append(exc)
        finally:
            session.close()

    threads = [threading.Thread(target=writer, args=(n,)) for n in range(_WRITERS)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    with factory() as session:
        entries = audit_log_service.list_entries(session, tenant.id)
        assert sorted(e.sequence for e in entries) == list(range(1, _WRITERS * _ENTRIES_EACH + 1))
        result = audit_log_service.verify_chain(session, tenant.id)
        assert (result.valid, result.checked_count) == (True, _WRITERS * _ENTRIES_EACH)
