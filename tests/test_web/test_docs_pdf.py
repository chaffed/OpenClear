# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import pytest

from openclear.services.doc_pdf_service import weasyprint_usable
from tests.conftest import TenantFactory
from tests.test_web.test_docs import _login

pytestmark = pytest.mark.skipif(
    not weasyprint_usable(), reason="weasyprint (or its system Pango/Cairo/GLib libraries) not available in this environment"
)


def test_end_user_pdf_downloads_for_permitted_user(client, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="docs-pdf-end-user")
    _login(client, tenant.slug, users["preparer"].email)

    resp = client.get("/ui/docs/end-user/pdf")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/pdf"
    assert resp.content[:5] == b"%PDF-"
    assert len(resp.content) > 1000


def test_end_user_pdf_forbidden_without_permission(client, db_session, tenant_factory):
    from openclear.services import security_group_service, user_service
    from openclear.services.security_group_service import SecurityGroupInput

    tenant, _account, _users = tenant_factory.make(slug="docs-pdf-end-user-forbidden")
    bare_group = security_group_service.create_security_group(
        db_session, tenant.id, SecurityGroupInput(name="No Docs", permissions=["issued_item:read"])
    )
    user = user_service.create_user_with_membership(
        db_session, tenant.id, email=f"bare@{tenant.slug}.example.com",
        password=TenantFactory.PASSWORD, security_group_id=bare_group.id,
    )
    db_session.commit()

    _login(client, tenant.slug, user.email)
    resp = client.get("/ui/docs/end-user/pdf", follow_redirects=False)
    assert resp.status_code == 403


def test_admin_pdf_downloads_for_admin(client, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="docs-pdf-admin")
    _login(client, tenant.slug, users["admin"].email)

    resp = client.get("/ui/docs/admin/pdf")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/pdf"
    assert resp.content[:5] == b"%PDF-"
    assert len(resp.content) > 1000


def test_admin_pdf_forbidden_for_preparer(client, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="docs-pdf-admin-forbidden")
    _login(client, tenant.slug, users["preparer"].email)

    resp = client.get("/ui/docs/admin/pdf", follow_redirects=False)
    assert resp.status_code == 403


def test_pdf_download_links_present_on_index_and_topic_pages(client, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="docs-pdf-links")
    _login(client, tenant.slug, users["admin"].email)

    index_resp = client.get("/ui/docs/admin")
    assert 'href="/ui/docs/admin/pdf"' in index_resp.text

    topic_resp = client.get("/ui/docs/admin/users-security-groups")
    assert 'href="/ui/docs/admin/pdf"' in topic_resp.text
