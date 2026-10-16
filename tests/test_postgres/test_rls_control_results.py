# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Control results and deviations under row-level security (CONTROLS_PLAN.md CT2): a page view
records its bank's results, and a bank sees only its own deviations."""

import pytest
from sqlalchemy import select

from openclear.domain.control_result import ControlTestResult
from openclear.services import control_results_service
from openclear.services.control_results_service import Outcome
from tests.conftest import TEST_DATABASE_URL, TenantFactory

pytestmark = pytest.mark.skipif(not TEST_DATABASE_URL, reason="needs OPENCLEAR_TEST_DATABASE_URL (Postgres)")


def test_a_bank_sees_only_its_own_results_and_deviations(rls_client, db_session, tenant_factory):
    mine, _account, users = tenant_factory.make(slug="rls-ctl-mine")
    other, _other_account, _ = tenant_factory.make(slug="rls-ctl-other")
    for tenant, key in ((mine, "sla"), (other, "scim_sync")):
        control_results_service.record(db_session, tenant.id, [Outcome(key, "FAIL", f"{key} failed", {})], started_by="schedule")
    db_session.commit()
    theirs = control_results_service.deviations(db_session, other.id)[0]

    rls_client.get("/ui/login")
    rls_client.post("/ui/login", data={"tenant_slug": mine.slug, "email": users["admin"].email, "password": TenantFactory.PASSWORD,
                                       "csrf_token": rls_client.cookies.get("csrf_token"), "next": "/ui/"})
    listing = rls_client.get("/ui/admin/controls/deviations")
    mine_dev = control_results_service.deviations(db_session, mine.id)[0]
    assert listing.status_code == 200 and f"/ui/admin/controls/deviations/{mine_dev.id}" in listing.text
    assert f"/ui/admin/controls/deviations/{theirs.id}" not in listing.text
    assert rls_client.get(f"/ui/admin/controls/deviations/{theirs.id}").status_code == 404

    assert rls_client.get("/ui/admin/controls").status_code == 200  # a view records its bank's changes under RLS
    db_session.expire_all()
    recorded = db_session.execute(select(ControlTestResult.tenant_id).where(ControlTestResult.started_by == "view")).scalars().all()
    assert recorded and set(recorded) == {mine.id}
