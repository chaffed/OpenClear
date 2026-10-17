# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Dual control under row-level security (DUAL_CONTROL_PLAN.md): a bank sees only its own change
requests, never the service provider's or its log; the provider's are written and read only
from a session bound to no bank."""

import pytest
from sqlalchemy import select
from sqlalchemy.exc import DBAPIError

from openclear.db.tenancy import bind_session_to_tenant
from openclear.domain.change_request import ChangeRequest
from openclear.domain.platform_action_entry import PlatformActionEntry
from openclear.services import change_request_service, platform_log_service
from tests.conftest import TEST_DATABASE_URL

pytestmark = pytest.mark.skipif(not TEST_DATABASE_URL, reason="needs OPENCLEAR_TEST_DATABASE_URL (Postgres)")


def _policy(**over) -> dict:
    return {"values": {"password_min_length": 12, "password_require_uppercase": True, "password_require_lowercase": True,
                       "password_require_number": True, "password_require_symbol": False, **over}}


def test_banks_and_the_provider_stay_apart(app_session_factory, db_session, tenant_factory):
    mine, _a, users = tenant_factory.make(slug="rls-dc-mine")
    other, _b, other_users = tenant_factory.make(slug="rls-dc-other")
    for tenant, people in ((mine, users), (other, other_users)):
        change_request_service.request_or_apply(db_session, "password_policy", _policy(), tenant_id=tenant.id, user_id=people["admin"].id)
        change_request_service.request_or_apply(db_session, "password_policy", _policy(password_min_length=8), tenant_id=tenant.id,
                                                user_id=people["admin"].id)
    db_session.commit()

    platform = app_session_factory()  # bound to no bank: the platform's
    try:
        platform_log_service.record(platform, action="platform.test", summary="Provider only")
        platform.add(ChangeRequest(tenant_id=None, scope="provider", kind="platform.role", setting="x", summary="Provider's", payload={},
                                   status="pending"))
        platform.commit()
    finally:
        platform.close()

    bank = app_session_factory()
    try:
        bind_session_to_tenant(bank, mine.id)
        seen = bank.execute(select(ChangeRequest)).scalars().all()
        assert {c.tenant_id for c in seen} == {mine.id}  # not the other bank's, not the provider's
        assert bank.execute(select(PlatformActionEntry)).scalars().all() == []
        bank.add(ChangeRequest(tenant_id=None, scope="provider", kind="platform.role", setting="y", summary="Forged", payload={}, status="pending"))
        with pytest.raises(DBAPIError):
            bank.flush()  # a bank can't write the provider's
    finally:
        bank.rollback()
        bank.close()


def test_a_bank_reads_the_providers_log_verification_but_not_the_log(app_session_factory, db_session, tenant_factory):
    from openclear.services import control_checks

    tenant, _a, _users = tenant_factory.make(slug="rls-dc-verify")
    platform = app_session_factory()  # bound to no bank, as the daily job is
    try:
        platform_log_service.record(platform, action="platform.test", summary="Provider only")
        platform_log_service.verify_and_keep(platform)
        platform.commit()
    finally:
        platform.close()

    bank = app_session_factory()
    try:
        bind_session_to_tenant(bank, tenant.id)
        assert bank.execute(select(PlatformActionEntry)).scalars().all() == []
        assert platform_log_service.verify(bank) == (True, 0, None)  # a bank's session would see an empty log, hence the kept result
        result = control_checks.provider_log(bank, tenant.id)
        assert result.status == "PASS" and result.evidence["entries_checked"] >= 1
    finally:
        bank.rollback()
        bank.close()
