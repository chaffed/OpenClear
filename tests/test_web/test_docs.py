# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import html

import pytest

from openclear.web.routers.docs import ADMIN_DOC_PAGES, END_USER_DOC_PAGES
from tests.conftest import TenantFactory


def _escaped(title: str) -> str:
    return html.escape(title, quote=False)


def _login(client, tenant_slug, email, password=TenantFactory.PASSWORD):
    client.get("/ui/login")
    csrf = client.cookies.get("csrf_token")
    client.post(
        "/ui/login", data={"tenant_slug": tenant_slug, "email": email, "password": password, "csrf_token": csrf, "next": "/ui/"}
    )
    return csrf


@pytest.mark.parametrize("page", END_USER_DOC_PAGES, ids=lambda p: p.slug)
def test_end_user_doc_page_renders_for_preparer(client, tenant_factory, page):
    tenant, _account, users = tenant_factory.make(slug=f"docs-end-user-{page.slug}")
    _login(client, tenant.slug, users["preparer"].email)

    resp = client.get(f"/ui/docs/end-user/{page.slug}")
    assert resp.status_code == 200
    assert _escaped(page.title) in resp.text


def test_end_user_doc_index_lists_all_topics(client, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="docs-end-user-index")
    _login(client, tenant.slug, users["viewer"].email)

    resp = client.get("/ui/docs/end-user")
    assert resp.status_code == 200
    for page in END_USER_DOC_PAGES:
        assert _escaped(page.title) in resp.text


def test_end_user_docs_forbidden_without_permission(client, db_session, tenant_factory):
    # No default security group lacks end_user_documentation:read today, so build a
    # custom, permission-less group to exercise the gate itself.
    from openclear.services import security_group_service, user_service
    from openclear.services.security_group_service import SecurityGroupInput

    tenant, _account, _users = tenant_factory.make(slug="docs-end-user-forbidden")
    bare_group = security_group_service.create_security_group(
        db_session, tenant.id, SecurityGroupInput(name="No Docs", permissions=["issued_item:read"])
    )
    user = user_service.create_user_with_membership(
        db_session, tenant.id, email=f"bare@{tenant.slug}.example.com",
        password=TenantFactory.PASSWORD, security_group_id=bare_group.id,
    )
    db_session.commit()

    _login(client, tenant.slug, user.email)
    resp = client.get("/ui/docs/end-user", follow_redirects=False)
    assert resp.status_code == 403


def test_end_user_doc_unknown_slug_404s(client, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="docs-end-user-404")
    _login(client, tenant.slug, users["admin"].email)

    resp = client.get("/ui/docs/end-user/not-a-real-topic")
    assert resp.status_code == 404


@pytest.mark.parametrize("page", ADMIN_DOC_PAGES, ids=lambda p: p.slug)
def test_admin_doc_page_renders_for_admin(client, tenant_factory, page):
    tenant, _account, users = tenant_factory.make(slug=f"docs-admin-{page.slug}")
    _login(client, tenant.slug, users["admin"].email)

    resp = client.get(f"/ui/docs/admin/{page.slug}")
    assert resp.status_code == 200
    assert _escaped(page.title) in resp.text


def test_admin_doc_index_lists_all_topics(client, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="docs-admin-index")
    _login(client, tenant.slug, users["admin"].email)

    resp = client.get("/ui/docs/admin")
    assert resp.status_code == 200
    for page in ADMIN_DOC_PAGES:
        assert _escaped(page.title) in resp.text


def test_admin_docs_forbidden_for_preparer(client, tenant_factory):
    # Preparer is one of the "everyday" default groups -- it gets end-user docs but not
    # the admin-only track, which is Admin-only by default.
    tenant, _account, users = tenant_factory.make(slug="docs-admin-forbidden")
    _login(client, tenant.slug, users["preparer"].email)

    resp = client.get("/ui/docs/admin", follow_redirects=False)
    assert resp.status_code == 403


def test_admin_doc_unknown_slug_404s(client, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="docs-admin-404")
    _login(client, tenant.slug, users["admin"].email)

    resp = client.get("/ui/docs/admin/not-a-real-topic")
    assert resp.status_code == 404


def test_nav_shows_both_links_for_admin_but_only_end_user_link_for_viewer(client, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="docs-nav-gating")

    _login(client, tenant.slug, users["admin"].email)
    admin_dashboard = client.get("/ui/")
    assert 'href="/ui/docs/end-user"' in admin_dashboard.text
    assert 'href="/ui/docs/admin"' in admin_dashboard.text

    _login(client, tenant.slug, users["viewer"].email)
    viewer_dashboard = client.get("/ui/")
    assert 'href="/ui/docs/end-user"' in viewer_dashboard.text
    assert 'href="/ui/docs/admin"' not in viewer_dashboard.text
