# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Every HTTP error a person can hit on a web page gets the branded page with a message that
fits it; the API keeps answering JSON (main.py). Database outages are a 503 with
Retry-After, and /health says so too."""

import pytest
from fastapi.testclient import TestClient

from openclear.config import get_settings
from tests.conftest import TenantFactory, login_headers


@pytest.fixture
def lenient(app):
    @app.get("/ui/__test_db_down")
    def _ui_db_down():
        from sqlalchemy.exc import OperationalError

        raise OperationalError("SELECT 1", {}, Exception("could not connect to server"))

    @app.get("/api/v1/__test_db_down")
    def _api_db_down():
        from sqlalchemy.exc import OperationalError

        raise OperationalError("SELECT 1", {}, Exception("could not connect to server"))

    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


def _branded(response, code: int, words: str):
    assert response.status_code == code, response.text[:300]
    assert response.headers["content-type"].startswith("text/html")
    assert words in response.text and f"Error code {code}" in response.text
    assert "Reference " + response.headers["x-request-id"] in response.text  # to quote to support


def _sign_in(client, tenant, email):
    client.get("/ui/login")
    client.post("/ui/login", data={"tenant_slug": tenant.slug, "email": email, "password": TenantFactory.PASSWORD,
                                   "csrf_token": client.cookies.get("csrf_token"), "next": "/ui/"})


def test_not_found_and_wrong_method(lenient):
    page = lenient.get("/ui/no-such-page")
    _branded(page, 404, "Page not found")
    assert 'action="/ui/search"' in page.text  # a way to find what they were after
    _branded(lenient.get("/some/old/bookmark"), 404, "Page not found")
    _branded(lenient.get("/ui/logout"), 405, "only works from a button")
    # The API and static files keep their usual answers.
    assert lenient.get("/api/v1/no-such-thing").json() == {"detail": "Not Found"}
    assert lenient.get("/static/no-such.css").status_code == 404


def test_an_expired_form_says_so(lenient, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="err-csrf")
    _sign_in(lenient, tenant, users["admin"].email)
    for data in ({"name": "x"}, {"name": "x", "csrf_token": "stale-token"}):
        _branded(lenient.post("/ui/settings", data=data), 403, "This form expired")


def test_a_broken_address_or_form(lenient, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="err-422")
    _sign_in(lenient, tenant, users["admin"].email)
    _branded(lenient.get("/ui/exceptions/not-an-id"), 422, "Part of that address or form")
    api = lenient.get("/api/v1/exceptions/not-an-id", headers=login_headers(lenient, tenant.slug, users["admin"].email))
    assert api.status_code == 422 and isinstance(api.json()["detail"], list)  # the API's usual validation detail


def test_too_large_and_too_many(lenient, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "max_request_body_bytes", 2 * 1024 * 1024)
    big = b"x" * (3 * 1024 * 1024)
    _branded(lenient.post("/ui/login", content=big, headers={"content-type": "application/x-www-form-urlencoded"}),
             413, "Files can be up to 2 MB")
    api = lenient.post("/api/v1/oauth/token", content=big, headers={"content-type": "application/x-www-form-urlencoded"})
    assert api.status_code == 413 and "too large" in api.json()["detail"]

    monkeypatch.setattr(settings, "rate_limit_per_minute", 2)
    for _ in range(2):
        lenient.get("/ui/login")
    limited = lenient.get("/ui/login")
    _branded(limited, 429, "Wait a minute")
    assert limited.headers["retry-after"] == "60"
    api = lenient.get("/api/v1/accounts")
    assert api.status_code == 429 and api.headers["retry-after"] == "60" and "try again" in api.json()["detail"]


def test_a_database_outage_is_a_503(lenient):
    page = lenient.get("/ui/__test_db_down")
    assert page.status_code == 503 and page.headers["retry-after"] == "30"
    assert "temporarily unavailable" in page.text and "could not connect" not in page.text
    api = lenient.get("/api/v1/__test_db_down")
    assert api.status_code == 503 and api.json()["detail"].startswith("Temporarily unavailable")
    assert api.json()["request_id"] == api.headers["x-request-id"]


def test_health_checks_the_database(app, lenient):
    assert lenient.get("/health").json() == {"status": "ok"}

    from openclear.db.session import get_db

    class _Down:
        def execute(self, *_a, **_k):
            raise ConnectionError("database is down")

        def close(self):
            pass

    saved = app.dependency_overrides[get_db]
    app.dependency_overrides[get_db] = lambda: _Down()
    try:
        down = lenient.get("/health")
    finally:
        app.dependency_overrides[get_db] = saved
    assert down.status_code == 503 and down.json()["status"] == "unavailable" and down.headers["retry-after"] == "30"


def test_the_static_pages_need_nothing_else():
    from pathlib import Path

    folder = Path(__file__).resolve().parents[2] / "src" / "openclear" / "static" / "errors"
    for code in ("502", "503", "504"):
        html = (folder / f"{code}.html").read_text()
        assert f"Error {code}" in html and "<script" not in html and "<link" not in html and "src=" not in html


def test_the_bare_address_goes_somewhere_useful(client, monkeypatch):
    assert client.get("/", follow_redirects=False).headers["location"] == "/ui/"
    monkeypatch.setattr(get_settings(), "sandbox_signup_enabled", True)
    assert client.get("/", follow_redirects=False).headers["location"] == "/ui/sandbox"  # the developer sandbox
