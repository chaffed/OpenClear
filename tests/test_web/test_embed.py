# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Embedding OpenClear in a bank's online banking (SDK_PLAN.md P2; services/embed_service.py):
launch links, framing only for an embedded session and only by its origin, the embedded page,
and what happens when the session ends."""

import pytest
from sqlalchemy import select

from openclear.domain.tenant import Tenant
from openclear.services import embed_service
from tests.test_api.test_obp_profile import _bank, _client, _exchange, _id_token, provider  # noqa: F401 -- fixture

OBP = "https://banking.acme-bank.example"


def _embedded_bank(db_session, tenant_factory, slug, provider):  # noqa: F811
    tenant, users, connection, acme, beta, account = _bank(db_session, tenant_factory, slug, provider)
    embed_service.set_allowed_origins(db_session, tenant.id, f"{OBP}\nhttps://other.example:8443/")
    db_session.commit()
    issued = _client(db_session, tenant, connection)
    return tenant, issued


def _person(client, issued, provider, **kwargs):  # noqa: F811
    return {"Authorization": f"Bearer {_exchange(client, issued, _id_token(provider, **kwargs)).json()['access_token']}"}


def _launch(client, headers, origin=OBP, path="/ui/exceptions"):
    return client.post("/api/v1/embed/launch", headers=headers, json={"parent_origin": origin, "path": path})


def test_origins_are_checked(db_session, tenant_factory):
    tenant, _a, _u = tenant_factory.make(slug="embed-origins")
    assert embed_service.set_allowed_origins(db_session, tenant.id, " https://Bank.Example/ \nhttps://bank.example\nhttps://x.example:443") == [
        "https://bank.example", "https://x.example"]
    for bad in ("http://bank.example", "https://bank.example/path", "https://*.bank.example", "bank.example", "https://u:p@bank.example"):
        with pytest.raises(embed_service.EmbedError):
            embed_service.set_allowed_origins(db_session, tenant.id, bad)


def test_a_launch_link_opens_a_framed_session_for_the_person(client, db_session, tenant_factory, provider):  # noqa: F811
    tenant, issued = _embedded_bank(db_session, tenant_factory, "embed-flow", provider)
    launch = _launch(client, _person(client, issued, provider))
    assert launch.status_code == 201, launch.text
    url = launch.json()["url"]
    assert "/ui/embed/launch?code=oclaunch_" in url and launch.json()["expires_in"] == 60

    client.cookies.clear()
    opened = client.get(url.replace("http://testserver", ""), follow_redirects=False)
    assert opened.status_code == 303 and opened.headers["location"] == "/ui/exceptions"
    page = client.get("/ui/exceptions")
    assert page.status_code == 200
    assert f"frame-ancestors {OBP}" in page.headers["content-security-policy"] and "x-frame-options" not in page.headers
    assert f'data-embed-parent="{OBP}"' in page.text and 'class="sidenav"' not in page.text

    reused = client.get(url.replace("http://testserver", ""), follow_redirects=False)
    assert reused.status_code == 400 and "already been used" in reused.text
    assert "frame-ancestors *" in reused.headers["content-security-policy"]  # the ended page can show in any frame

    # The bank stops allowing that origin: the session's pages can't be framed any more.
    db_session.get(Tenant, tenant.id).embed_allowed_origins = "https://other.example:8443"
    db_session.commit()
    after = client.get("/ui/exceptions")
    assert "frame-ancestors 'none'" in after.headers["content-security-policy"] and after.headers["x-frame-options"] == "DENY"

    logout = client.post("/ui/logout", data={"csrf_token": client.cookies.get("csrf_token")}, follow_redirects=False)
    assert logout.headers["location"] == "/ui/embed/ended"


def test_launches_that_must_be_refused(client, db_session, tenant_factory, provider):  # noqa: F811
    tenant, issued = _embedded_bank(db_session, tenant_factory, "embed-refused", provider)
    person = _person(client, issued, provider)
    assert _launch(client, person, origin="https://evil.example").status_code == 422
    assert _launch(client, person, path="https://evil.example/").status_code == 422
    assert _launch(client, person, path="/ui/login").status_code == 422
    plain = client.post("/api/v1/oauth/token", data={"grant_type": "client_credentials", "client_id": issued.client.client_id,
                                                     "client_secret": issued.secret}).json()["access_token"]
    assert _launch(client, {"Authorization": f"Bearer {plain}"}).status_code == 403  # the client itself isn't a person

    url = _launch(client, person).json()["url"].replace("http://testserver", "")
    from openclear.domain.embed_launch import EmbedLaunch
    from datetime import datetime, timedelta, timezone

    row = db_session.execute(select(EmbedLaunch)).scalars().all()[-1]
    row.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db_session.commit()
    expired = client.get(url, follow_redirects=False)
    assert expired.status_code == 400 and "expired" in expired.text
    assert client.get("/ui/embed/launch?code=nonsense").status_code == 400


def test_ordinary_sessions_are_never_framed(client, db_session, tenant_factory):
    from tests.conftest import TenantFactory

    tenant, _a, users = tenant_factory.make(slug="embed-ordinary")
    client.get("/ui/login")
    csrf = client.cookies.get("csrf_token")
    client.post("/ui/login", data={"tenant_slug": tenant.slug, "email": users["admin"].email, "password": TenantFactory.PASSWORD,
                                   "csrf_token": csrf, "next": "/ui/"})
    page = client.get("/ui/exceptions?event=exception-decided")
    assert "frame-ancestors 'none'" in page.headers["content-security-policy"] and page.headers["x-frame-options"] == "DENY"
    assert "data-embed" not in page.text and 'class="sidenav"' in page.text


def test_framed_cookies_are_partitioned_over_https(monkeypatch):
    from datetime import datetime, timedelta, timezone

    from fastapi.responses import Response

    from openclear.auth.security import SessionTokens
    from openclear.web import security

    monkeypatch.setattr(security, "_secure_cookies", lambda: True)
    end = datetime.now(timezone.utc) + timedelta(hours=1)
    response = Response()
    security.set_session_cookies(response, SessionTokens("a", "r", None, end, end, embed={"po": OBP}))
    cookies = [v for k, v in response.raw_headers if k == b"set-cookie"]
    assert len(cookies) == 2 and all(b"SameSite=none" in c and b"Partitioned" in c and b"Secure" in c for c in cookies)
    ordinary = Response()
    security.set_session_cookies(ordinary, SessionTokens("a", "r", None, end, end))
    assert not any(b"Partitioned" in v for k, v in ordinary.raw_headers if k == b"set-cookie")
