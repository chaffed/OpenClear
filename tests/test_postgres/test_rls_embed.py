# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Embedding under row-level security (SDK_PLAN.md P2): a launch link arrives with no
session, so its code must bind the request to its bank."""

import pytest

from tests.conftest import TEST_DATABASE_URL
from tests.test_api.test_obp_profile import provider  # noqa: F401 -- fixture
from tests.test_web.test_embed import OBP, _embedded_bank, _launch, _person

pytestmark = pytest.mark.skipif(not TEST_DATABASE_URL, reason="needs OPENCLEAR_TEST_DATABASE_URL (Postgres)")


def test_launch_and_embedded_pages_under_rls(rls_client, db_session, tenant_factory, provider):  # noqa: F811
    _tenant, issued = _embedded_bank(db_session, tenant_factory, "rls-embed", provider)
    launch = _launch(rls_client, _person(rls_client, issued, provider))
    assert launch.status_code == 201, launch.text
    rls_client.cookies.clear()
    opened = rls_client.get(launch.json()["url"].replace("http://testserver", ""), follow_redirects=False)
    assert opened.status_code == 303, opened.text
    page = rls_client.get("/ui/exceptions")
    assert page.status_code == 200 and f"frame-ancestors {OBP}" in page.headers["content-security-policy"]
