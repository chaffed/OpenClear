# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Postgres enforces column lengths (SQLite doesn't), so an over-long value used to reach
the database and come back as an error page. It's now a normal form message, or a 400
from the API. Runs only with OPENCLEAR_TEST_DATABASE_URL set."""

import pytest

from tests.conftest import TEST_DATABASE_URL, TenantFactory, login_headers

pytestmark = pytest.mark.skipif(not TEST_DATABASE_URL, reason="needs OPENCLEAR_TEST_DATABASE_URL (Postgres)")


def test_an_over_long_customer_number_is_a_form_message(client, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="long-values-web")
    client.get("/ui/login")
    csrf = client.cookies.get("csrf_token")
    client.post(
        "/ui/login",
        data={"tenant_slug": tenant.slug, "email": users["admin"].email, "password": TenantFactory.PASSWORD, "csrf_token": csrf, "next": "/ui/"},
    )

    resp = client.post("/ui/customers", data={"csrf_token": csrf, "customer_number": "C" * 100, "name": "Too Long Inc"})

    assert resp.status_code == 422
    assert "too long or not valid" in resp.text


def test_an_over_long_value_through_the_api_is_a_400(client, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="long-values-api")
    headers = login_headers(client, tenant.slug, users["preparer"].email)

    resp = client.post(
        "/api/v1/issued-items",
        headers=headers,
        json={"account_id": str(account.id), "check_number": "9" * 60, "amount": "10.00", "payee_name": "X", "issue_date": "2026-01-01"},
    )

    assert resp.status_code == 400
    assert resp.json()["detail"] == "A value is too long or not valid for its field"
