# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Access reviews under row-level security (CONTROLS_PLAN.md CT3): starting one reads only the
bank's own memberships, and another bank's review is never found."""

import pytest

from openclear.services import access_review_service
from tests.conftest import TEST_DATABASE_URL, TenantFactory

pytestmark = pytest.mark.skipif(not TEST_DATABASE_URL, reason="needs OPENCLEAR_TEST_DATABASE_URL (Postgres)")


def test_a_banks_review_holds_only_its_own_people(rls_client, db_session, tenant_factory):
    mine, _account, users = tenant_factory.make(slug="rls-ar-mine")
    other, _other_account, _ = tenant_factory.make(slug="rls-ar-other")
    theirs = access_review_service.start(db_session, other.id, None, user_id=None)
    db_session.commit()

    rls_client.get("/ui/login")
    rls_client.post("/ui/login", data={"tenant_slug": mine.slug, "email": users["admin"].email, "password": TenantFactory.PASSWORD,
                                       "csrf_token": rls_client.cookies.get("csrf_token"), "next": "/ui/"})
    started = rls_client.post("/ui/access-reviews", data={"csrf_token": rls_client.cookies.get("csrf_token")}, follow_redirects=False)
    assert started.status_code == 303
    page = rls_client.get(started.headers["location"]).text
    assert users["viewer"].email in page and "rls-ar-other" not in page
    assert rls_client.get(f"/ui/access-reviews/{theirs.id}").status_code == 404
