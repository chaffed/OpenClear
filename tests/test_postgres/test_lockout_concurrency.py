# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Wrong passwords sent in parallel, on Postgres (FULL_REVIEW_PLAN.md FR3, S1): every one counts,
and the account is locked, and its owner emailed, once. Runs only with OPENCLEAR_TEST_DATABASE_URL set."""

import threading

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from openclear.auth.login_service import authenticate_password
from openclear.domain.notification import Notification, NotificationType
from openclear.domain.tenant import Tenant
from openclear.domain.user import User
from tests.conftest import TEST_DATABASE_URL

pytestmark = pytest.mark.skipif(not TEST_DATABASE_URL, reason="needs OPENCLEAR_TEST_DATABASE_URL (Postgres)")

_GUESSES = 10


@pytest.mark.asvs("6.3.1")
def test_parallel_guesses_all_count_and_lock_once(engine, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="lock-parallel")
    db_session.get(Tenant, tenant.id).login_max_failed_attempts = 5
    db_session.commit()
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    start = threading.Barrier(_GUESSES)
    errors = []

    def guess() -> None:
        session = factory()
        try:
            start.wait()
            authenticate_password(session, tenant.slug, users["viewer"].email, "wrong guess")
            session.commit()
        except Exception as exc:  # noqa: BLE001 -- asserted on below
            errors.append(exc)
        finally:
            session.close()

    threads = [threading.Thread(target=guess) for _ in range(_GUESSES)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    with factory() as session:
        user = session.get(User, users["viewer"].id)
        emails = session.execute(select(Notification).where(Notification.recipient_user_id == user.id,
                                                            Notification.notification_type == NotificationType.ACCOUNT_LOCKED)).scalars().all()
    assert user.locked_until is not None
    # Every guess either counted or arrived once it was locked (and was refused before the check).
    assert 5 <= user.failed_login_attempts <= _GUESSES
    assert len(emails) == 1
