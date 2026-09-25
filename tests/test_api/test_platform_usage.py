# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import csv
import io

from openclear.services import platform_api_key_service
from tests.conftest import login_headers


def test_no_key_is_rejected(client):
    resp = client.get("/api/v1/platform/usage", params={"period_start": "2026-01-01", "period_end": "2026-12-31"})
    assert resp.status_code == 401


def test_bad_key_is_rejected(client):
    resp = client.get(
        "/api/v1/platform/usage", params={"period_start": "2026-01-01", "period_end": "2026-12-31"},
        headers={"X-Api-Key": "not-a-real-key"},
    )
    assert resp.status_code == 401


def test_revoked_key_is_rejected(client, db_session):
    row, raw_key = platform_api_key_service.generate_and_create(db_session, "Test Integration")
    db_session.commit()
    platform_api_key_service.revoke(db_session, row.id)
    db_session.commit()

    resp = client.get(
        "/api/v1/platform/usage", params={"period_start": "2026-01-01", "period_end": "2026-12-31"},
        headers={"X-Api-Key": raw_key},
    )
    assert resp.status_code == 401


def test_tenant_jwt_does_not_work_on_this_route(client, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="platform-usage-jwt-rejected")
    headers = login_headers(client, tenant.slug, users["admin"].email)

    resp = client.get("/api/v1/platform/usage", params={"period_start": "2026-01-01", "period_end": "2026-12-31"}, headers=headers)
    assert resp.status_code == 401


def test_valid_key_returns_json_for_every_tenant(client, db_session, tenant_factory):
    tenant_a, _account_a, _users_a = tenant_factory.make(slug="platform-usage-json-a")
    tenant_b, _account_b, _users_b = tenant_factory.make(slug="platform-usage-json-b")
    _row, raw_key = platform_api_key_service.generate_and_create(db_session, "Test Integration")
    db_session.commit()

    resp = client.get(
        "/api/v1/platform/usage", params={"period_start": "2000-01-01", "period_end": "2100-12-31"},
        headers={"X-Api-Key": raw_key},
    )
    assert resp.status_code == 200
    body = resp.json()
    slugs = {t["tenant_slug"] for t in body["tenants"]}
    assert {tenant_a.slug, tenant_b.slug} <= slugs
    assert body["period_start"] == "2000-01-01"


def test_valid_key_scopes_to_one_tenant(client, db_session, tenant_factory):
    tenant_a, _account_a, _users_a = tenant_factory.make(slug="platform-usage-scoped-a")
    tenant_b, _account_b, _users_b = tenant_factory.make(slug="platform-usage-scoped-b")
    _row, raw_key = platform_api_key_service.generate_and_create(db_session, "Test Integration")
    db_session.commit()

    resp = client.get(
        "/api/v1/platform/usage",
        params={"period_start": "2000-01-01", "period_end": "2100-12-31", "tenant_id": str(tenant_a.id)},
        headers={"X-Api-Key": raw_key},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["tenants"]) == 1
    assert body["tenants"][0]["tenant_slug"] == tenant_a.slug


def test_unknown_tenant_id_404s(client, db_session):
    import uuid

    _row, raw_key = platform_api_key_service.generate_and_create(db_session, "Test Integration")
    db_session.commit()

    resp = client.get(
        "/api/v1/platform/usage",
        params={"period_start": "2000-01-01", "period_end": "2100-12-31", "tenant_id": str(uuid.uuid4())},
        headers={"X-Api-Key": raw_key},
    )
    assert resp.status_code == 404


def test_period_end_before_period_start_rejected(client, db_session):
    _row, raw_key = platform_api_key_service.generate_and_create(db_session, "Test Integration")
    db_session.commit()

    resp = client.get(
        "/api/v1/platform/usage", params={"period_start": "2026-06-01", "period_end": "2026-01-01"},
        headers={"X-Api-Key": raw_key},
    )
    assert resp.status_code == 422


def test_csv_format_has_expected_header_and_one_row_per_tenant(client, db_session, tenant_factory):
    tenant, _account, _users = tenant_factory.make(slug="platform-usage-csv")
    _row, raw_key = platform_api_key_service.generate_and_create(db_session, "Test Integration")
    db_session.commit()

    resp = client.get(
        "/api/v1/platform/usage",
        params={"period_start": "2000-01-01", "period_end": "2100-12-31", "tenant_id": str(tenant.id), "format": "csv"},
        headers={"X-Api-Key": raw_key},
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    reader = csv.DictReader(io.StringIO(resp.text))
    rows = list(reader)
    assert reader.fieldnames[:3] == ["tenant_id", "tenant_slug", "tenant_name"]
    assert len(rows) == 1
    assert rows[0]["tenant_slug"] == tenant.slug
