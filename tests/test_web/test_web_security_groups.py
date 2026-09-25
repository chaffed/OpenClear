# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

from fastapi.testclient import TestClient

from openclear.services import security_group_service
from tests.conftest import TenantFactory


def _login(client, tenant_slug, email, password=TenantFactory.PASSWORD):
    client.get("/ui/login")
    csrf = client.cookies.get("csrf_token")
    client.post(
        "/ui/login", data={"tenant_slug": tenant_slug, "email": email, "password": password, "csrf_token": csrf, "next": "/ui/"}
    )
    return csrf


def test_security_groups_page_requires_permission(client, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="web-sg-forbidden")
    _login(client, tenant.slug, users["viewer"].email)

    resp = client.get("/ui/security-groups", follow_redirects=False)
    assert resp.status_code == 403


def test_create_security_group(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="web-sg-create")
    csrf = _login(client, tenant.slug, users["admin"].email)

    resp = client.post(
        "/ui/security-groups",
        data={"csrf_token": csrf, "name": "AP Clerk", "permissions": ["issued_item:read", "issued_item:write"]},
        follow_redirects=False,
    )
    assert resp.status_code == 303

    group = security_group_service.get_security_group_by_name(db_session, tenant.id, "AP Clerk")
    assert group is not None
    assert set(group.permissions) == {"issued_item:read", "issued_item:write"}


def test_edit_security_group(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="web-sg-edit")
    csrf = _login(client, tenant.slug, users["admin"].email)
    group = security_group_service.get_security_group_by_name(db_session, tenant.id, "Viewer")

    resp = client.post(
        f"/ui/security-groups/{group.id}",
        data={"csrf_token": csrf, "name": "Read Only", "permissions": ["issued_item:read"]},
        follow_redirects=False,
    )
    assert resp.status_code == 303

    # the POST committed through a different session (the request's own, via
    # _override_get_db) — expire this session's identity map so the re-fetch below
    # doesn't just return the pre-edit object it already has cached.
    db_session.expire_all()
    updated = security_group_service.get_security_group(db_session, tenant.id, group.id)
    assert updated.name == "Read Only"
    assert updated.permissions == ["issued_item:read"]


def test_editing_a_group_takes_effect_on_next_request_without_relogin(app, db_session, tenant_factory):
    """Permissions resolve fresh from the DB every request (auth/deps.py) — this proves
    an admin narrowing a group's permissions immediately blocks a page that group's user
    is still logged into with an already-issued cookie, no re-login or token expiry
    required. Two independent clients (separate cookie jars) keep both sessions alive at
    once — a single shared client would overwrite the preparer's cookies when the admin
    logs in to make the edit."""
    tenant, _account, users = tenant_factory.make(slug="web-sg-immediate")
    preparer_group = security_group_service.get_security_group_by_name(db_session, tenant.id, "Preparer")

    with TestClient(app) as admin_client, TestClient(app) as preparer_client:
        admin_csrf = _login(admin_client, tenant.slug, users["admin"].email)
        _login(preparer_client, tenant.slug, users["preparer"].email)

        # preparer can reach the write-gated page right now
        resp = preparer_client.get("/ui/issued-items/new")
        assert resp.status_code == 200

        # admin strips issued_item:write from Preparer
        edit_resp = admin_client.post(
            f"/ui/security-groups/{preparer_group.id}",
            data={"csrf_token": admin_csrf, "name": "Preparer", "permissions": ["issued_item:read"]},
        )
        assert edit_resp.status_code == 200

        # preparer's SAME still-valid cookies now get rejected — no re-login happened
        resp = preparer_client.get("/ui/issued-items/new", follow_redirects=False)
        assert resp.status_code == 403
