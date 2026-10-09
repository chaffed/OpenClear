# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Federation under row-level security (SDK_PLAN.md phase F): token exchange binds to the
client's bank before matching the person, and the exchanged token works as theirs."""

import pytest

from tests.conftest import TEST_DATABASE_URL
from tests.test_api.test_obp_profile import _bank, _client, _exchange, _id_token, provider  # noqa: F401 -- fixture

pytestmark = pytest.mark.skipif(not TEST_DATABASE_URL, reason="needs OPENCLEAR_TEST_DATABASE_URL (Postgres)")


def test_token_exchange_under_rls(rls_client, db_session, tenant_factory, provider):  # noqa: F811
    tenant, _users, connection, _acme, _beta, _a = _bank(db_session, tenant_factory, "rls-obp", provider)
    issued = _client(db_session, tenant, connection)
    exchanged = _exchange(rls_client, issued, _id_token(provider))
    assert exchanged.status_code == 200, exchanged.text
    headers = {"Authorization": f"Bearer {exchanged.json()['access_token']}"}
    assert rls_client.get("/api/v1/issued-items", headers=headers).status_code == 200
