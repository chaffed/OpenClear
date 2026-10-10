# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""App-wide fallbacks for errors no individual route handles: a database uniqueness
conflict becomes a friendly 409, and anything else unexpected becomes a branded 500 page
(or a JSON 500 under /api) instead of a bare "Internal Server Error" with no way back."""

import pytest
from fastapi.testclient import TestClient

from tests.conftest import TenantFactory


def _login(client, tenant_slug, email):
    client.get("/ui/login")
    return client.post(
        "/ui/login",
        data={"tenant_slug": tenant_slug, "email": email, "password": TenantFactory.PASSWORD,
              "csrf_token": client.cookies.get("csrf_token"), "next": "/ui/"},
    )


@pytest.fixture
def lenient_client(app):
    """Like the shared `client` fixture, but lets the app's own error handlers produce the
    response instead of TestClient re-raising the server-side exception into the test."""

    @app.get("/ui/__test_boom")
    def _ui_boom():
        raise RuntimeError("internal-detail-xyz")

    @app.get("/ui/__test_conflict")
    def _ui_conflict():
        from sqlalchemy.exc import IntegrityError

        raise IntegrityError("INSERT ...", {}, Exception("UNIQUE constraint failed: thing.name"))

    @app.get("/api/v1/__test_boom")
    def _api_boom():
        raise RuntimeError("boom")

    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


def test_an_unhandled_uniqueness_conflict_is_a_friendly_409(lenient_client):
    """The app-wide backstop for any form that doesn't catch a duplicate itself."""
    resp = lenient_client.get("/ui/__test_conflict")

    assert resp.status_code == 409
    assert "already exists" in resp.text
    assert "UNIQUE constraint" not in resp.text


def test_duplicate_security_group_name_is_shown_on_the_form(lenient_client, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="web-err-dup-group")
    _login(lenient_client, tenant.slug, users["admin"].email)
    csrf = lenient_client.cookies.get("csrf_token")

    resp = lenient_client.post(
        "/ui/security-groups", data={"name": "Admin", "permissions": ["exception:read"], "csrf_token": csrf},
        follow_redirects=False,
    )

    assert resp.status_code == 400
    assert "A security group with that name already exists" in resp.text
    assert 'value="Admin"' in resp.text and 'value="exception:read" checked' in resp.text


def test_duplicate_customer_number_shows_a_plain_message_not_database_text(lenient_client, db_session, tenant_factory):
    from openclear.services import customer_service

    tenant, _account, users = tenant_factory.make(slug="web-err-dup-customer")
    customer_service.create_customer(db_session, tenant.id, customer_service.CustomerInput(customer_number="C-1", name="First"))
    db_session.commit()
    _login(lenient_client, tenant.slug, users["admin"].email)

    resp = lenient_client.post(
        "/ui/customers", data={"customer_number": "C-1", "name": "Second", "csrf_token": lenient_client.cookies.get("csrf_token")},
        follow_redirects=False,
    )

    assert resp.status_code == 422
    assert "A customer with that customer number already exists." in resp.text
    assert "IntegrityError" not in resp.text and "UNIQUE" not in resp.text and "INSERT" not in resp.text


def test_duplicate_issued_check_number_shows_a_plain_message(lenient_client, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="web-err-dup-issued")
    _login(lenient_client, tenant.slug, users["admin"].email)
    form = {"account_id": str(account.id), "check_number": "777", "amount": "10.00", "payee_name": "X",
            "issue_date": "2026-01-01", "csrf_token": lenient_client.cookies.get("csrf_token")}

    lenient_client.post("/ui/issued-items", data=form, follow_redirects=False)
    resp = lenient_client.post("/ui/issued-items", data=form, follow_redirects=False)

    assert "An issued check with that number already exists on this account." in resp.text
    assert "UNIQUE" not in resp.text


def test_friendly_error_keeps_service_messages_and_hides_the_rest():
    from openclear.web.form_errors import friendly_error

    assert friendly_error(ValueError("Account not found"), action="Could not create") == "Could not create: Account not found"
    assert "secret" not in friendly_error(RuntimeError("secret internals"), action="Could not create")


def test_unexpected_ui_error_renders_branded_500_page(lenient_client):
    resp = lenient_client.get("/ui/__test_boom")

    assert resp.status_code == 500
    assert "<html" in resp.text.lower()
    assert "Something went wrong" in resp.text
    assert "internal-detail-xyz" not in resp.text  # never leak exception details to the browser


def test_unexpected_api_error_returns_json_500(lenient_client):
    resp = lenient_client.get("/api/v1/__test_boom")

    assert resp.status_code == 500
    body = resp.json()
    assert body["detail"] == "Internal server error"
    assert body["request_id"] == resp.headers["x-request-id"]  # to quote to support; no exception text
