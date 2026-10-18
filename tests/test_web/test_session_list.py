# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed
"""The sessions list (OWASP_PLAN.md OW-D29; ASVS 5.0 7.4.5, 7.5.2): each session is recorded on its
first request; a person sees and ends their own; an admin signs everyone at the bank out."""

import pytest
from fastapi.testclient import TestClient

from openclear.domain.login_session import LoginSession
from tests.conftest import TenantFactory


def _sign_in(client, tenant, user, *, user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) Chrome/130.0 Safari/537.36"):
    client.headers["user-agent"] = user_agent
    client.get("/ui/login")
    client.post("/ui/login", data={"tenant_slug": tenant.slug, "email": user.email, "password": TenantFactory.PASSWORD,
                                   "csrf_token": client.cookies.get("csrf_token"), "next": "/ui/"})
    client.get("/ui/")  # the first request with the session records it
    return client.cookies.get("csrf_token")


@pytest.mark.asvs("7.5.2")
def test_a_person_sees_their_sessions_and_ends_another(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="sessions-list")
    viewer = users["viewer"]
    phone = TestClient(client.app)
    _sign_in(phone, tenant, viewer, user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 18_0) Safari/604.1")
    csrf = _sign_in(client, tenant, viewer)

    page = client.get("/ui/security").text
    assert "Chrome on macOS" in page and "Safari on iPhone" in page and "This one" in page
    other = db_session.query(LoginSession).filter(LoginSession.user_id == viewer.id,
                                                  LoginSession.user_agent.like("%iPhone%")).one()
    client.post(f"/ui/security/sessions/{other.id}/end", data={"csrf_token": csrf})

    assert phone.get("/ui/", follow_redirects=False).status_code == 303  # the phone is signed out
    assert client.get("/ui/", follow_redirects=False).status_code == 200  # this one isn't
    assert "Safari on iPhone" not in client.get("/ui/security").text


@pytest.mark.asvs("7.4.5")
def test_an_admin_signs_everyone_out_but_themselves(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="sessions-everyone")
    others = []
    for key in ("viewer", "preparer"):
        browser = TestClient(client.app)
        _sign_in(browser, tenant, users[key])
        others.append(browser)
    csrf = _sign_in(client, tenant, users["admin"])

    done = client.post("/ui/users/sign-out-everyone", data={"csrf_token": csrf}, follow_redirects=False)

    assert "2%20sessions%20ended" in done.headers["location"]
    assert all(b.get("/ui/", follow_redirects=False).status_code == 303 for b in others)
    assert client.get("/ui/", follow_redirects=False).status_code == 200


def test_sign_out_everywhere_ends_the_listed_sessions_too(client, db_session, tenant_factory):
    from openclear.services import session_service

    tenant, _account, users = tenant_factory.make(slug="sessions-everywhere")
    viewer = users["viewer"]
    _sign_in(TestClient(client.app), tenant, viewer)
    assert len(session_service.active_sessions(db_session, tenant.id, [viewer.id])) == 1
    session_service.revoke_all_sessions(db_session, viewer)
    db_session.commit()
    assert session_service.active_sessions(db_session, tenant.id, [viewer.id]) == []
