# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""API list endpoints page their results (api/v1/pagination.py): `limit` and `offset`,
oldest first, with X-Total-Count and a Link rel="next" header while there are more."""

from urllib.parse import parse_qs, urlparse

from tests.conftest import login_headers


def _present_checks(client, headers, account, count, prefix="PG"):
    ids = []
    for n in range(count):
        resp = client.post(
            "/api/v1/paid-items",
            headers=headers,
            json={"account_id": str(account.id), "check_number": f"{prefix}{n}", "presented_amount": "10.00", "presented_date": "2026-01-10"},
        )
        assert resp.status_code == 201, resp.text
        ids.append(resp.json()["id"])
    return ids


def _next_link(resp):
    link = resp.headers.get("link")
    if not link:
        return None
    url = link.split(";")[0].strip("<> ")
    parsed = urlparse(url)
    return f"{parsed.path}?{parsed.query}"


def test_paid_items_are_paged_oldest_first_with_total_and_next_link(client, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="api-paging")
    headers = login_headers(client, tenant.slug, users["preparer"].email)
    created = _present_checks(client, headers, account, 5)

    seen, url, pages = [], "/api/v1/paid-items?limit=2", 0
    while url:
        resp = client.get(url, headers=headers)
        assert resp.status_code == 200
        assert resp.headers["x-total-count"] == "5"
        seen += [row["id"] for row in resp.json()]
        url, pages = _next_link(resp), pages + 1

    assert pages == 3
    # Every row exactly once across the pages. (Oldest first by created_at, ties broken by
    # id: SQLite stores whole seconds, so rows made in one second don't keep creation order.)
    assert sorted(seen) == sorted(created)
    assert len(set(seen)) == len(seen)


def test_the_next_link_keeps_the_callers_filters(client, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="api-paging-filters")
    headers = login_headers(client, tenant.slug, users["preparer"].email)
    _present_checks(client, headers, account, 3)

    resp = client.get(f"/api/v1/paid-items?account_id={account.id}&limit=1", headers=headers)
    query = parse_qs(urlparse(resp.headers["link"].split(";")[0].strip("<> ")).query)
    assert query["account_id"] == [str(account.id)]
    assert query["offset"] == ["1"]


def test_exceptions_are_paged_too(client, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="api-paging-exceptions")
    headers = login_headers(client, tenant.slug, users["preparer"].email)
    _present_checks(client, headers, account, 3)  # no issued checks, so each raises an exception

    resp = client.get("/api/v1/exceptions?limit=2", headers=headers)
    assert resp.status_code == 200
    assert len(resp.json()) == 2
    assert resp.headers["x-total-count"] == "3"
    assert "rel=\"next\"" in resp.headers["link"]


def test_the_last_page_has_no_next_link_and_the_default_page_is_500(client, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="api-paging-last")
    headers = login_headers(client, tenant.slug, users["preparer"].email)
    _present_checks(client, headers, account, 2)

    resp = client.get("/api/v1/paid-items", headers=headers)
    assert len(resp.json()) == 2
    assert "link" not in resp.headers


def test_page_size_is_capped_at_1000(client, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="api-paging-cap")
    headers = login_headers(client, tenant.slug, users["preparer"].email)

    assert client.get("/api/v1/paid-items?limit=1001", headers=headers).status_code == 422
    assert client.get("/api/v1/paid-items?limit=1000", headers=headers).status_code == 200
