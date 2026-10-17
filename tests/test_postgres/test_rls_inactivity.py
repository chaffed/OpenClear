# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Deactivating and removing inactive access under row-level security
(services/inactivity_service.py): the daily sweep, run as the app's own role, binds to each bank
in turn and touches only that bank's people."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from openclear.domain.tenant import Tenant
from openclear.domain.tenant_membership import TenantMembership
from openclear.services import inactivity_service
from tests.conftest import TEST_DATABASE_URL

pytestmark = pytest.mark.skipif(not TEST_DATABASE_URL, reason="needs OPENCLEAR_TEST_DATABASE_URL (Postgres)")


def test_the_sweep_works_bank_by_bank(app_session_factory, db_session, tenant_factory):
    on, _a, on_users = tenant_factory.make(slug="rls-idle-on")
    off, _b, off_users = tenant_factory.make(slug="rls-idle-off")
    on.deactivate_inactive, on.deactivate_inactive_days = True, 30
    long_ago = datetime.now(timezone.utc) - timedelta(days=60)
    for tenant in (on, off):
        for membership in db_session.execute(select(TenantMembership).where(TenantMembership.tenant_id == tenant.id)).scalars():
            membership.last_active_at = long_ago
    db_session.commit()

    app = app_session_factory()  # bound to no bank, as the daily job starts
    try:
        assert inactivity_service.sweep_all(app) >= 4  # all but the kept administrator
    finally:
        app.close()

    db_session.expire_all()
    active = dict(db_session.execute(select(TenantMembership.tenant_id, TenantMembership.is_active)
                                     .where(TenantMembership.user_id == off_users["viewer"].id)).all())
    assert active[off.id] is True  # the other bank's rule is off
    on_active = db_session.execute(select(TenantMembership).where(TenantMembership.tenant_id == on.id,
                                                                  TenantMembership.is_active.is_(True))).scalars().all()
    assert len(on_active) == 1  # only its last administrator
    assert db_session.get(Tenant, on.id).deactivate_inactive


def test_a_sign_in_through_the_apps_role_records_activity(rls_client, db_session, tenant_factory):
    from tests.conftest import login_headers

    tenant, _account, users = tenant_factory.make(slug="rls-idle-signin")
    login_headers(rls_client, tenant.slug, users["viewer"].email)
    db_session.expire_all()
    membership = db_session.execute(select(TenantMembership).where(TenantMembership.tenant_id == tenant.id,
                                                                   TenantMembership.user_id == users["viewer"].id)).scalar_one()
    assert membership.last_active_at is not None
