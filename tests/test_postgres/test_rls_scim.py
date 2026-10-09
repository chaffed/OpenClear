# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""SCIM under row-level security (SDK_PLAN.md phase F): the directory calls before any
session exists, so the token must bind the request to its bank; and one bank's token never
reaches another bank's people."""

import pytest

from tests.conftest import TEST_DATABASE_URL
from tests.test_api.test_scim import _connection, _user_payload

pytestmark = pytest.mark.skipif(not TEST_DATABASE_URL, reason="needs OPENCLEAR_TEST_DATABASE_URL (Postgres)")


def test_scim_works_under_rls_and_stays_in_its_bank(rls_client, db_session, tenant_factory):
    mine, _a, _u = tenant_factory.make(slug="rls-scim-mine")
    other, _b, _v = tenant_factory.make(slug="rls-scim-other")
    _c, headers = _connection(db_session, mine)
    _d, other_headers = _connection(db_session, other)

    user_id = rls_client.post("/scim/v2/Users", headers=headers, json=_user_payload("pat@mine.example")).json()["id"]
    group = rls_client.post("/scim/v2/Groups", headers=headers, json={"displayName": "openclear-approvers", "members": [{"value": user_id}]})
    assert group.status_code == 201, group.text
    assert rls_client.get(f"/scim/v2/Users/{user_id}", headers=headers).json()["groups"][0]["display"] == "openclear-approvers"

    assert rls_client.get(f"/scim/v2/Users/{user_id}", headers=other_headers).status_code == 404
    assert rls_client.get("/scim/v2/Users", headers=other_headers).json()["totalResults"] == 0
