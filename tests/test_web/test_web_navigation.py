# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

from tests.conftest import TenantFactory


def _login(client, tenant_slug, email, password=TenantFactory.PASSWORD):
    client.get("/ui/login")
    csrf = client.cookies.get("csrf_token")
    client.post(
        "/ui/login", data={"tenant_slug": tenant_slug, "email": email, "password": password, "csrf_token": csrf, "next": "/ui/"}
    )


def test_nav_marks_current_page_active(client, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="nav-active-state")
    _login(client, tenant.slug, users["admin"].email)

    resp = client.get("/ui/issued-items")
    assert resp.status_code == 200
    # Nav links render an icon before the label (templates/_macros/icon.html), so match
    # on the opening tag + class rather than the full element contents.
    assert '<a href="/ui/issued-items" class="active">' in resp.text
    # a different nav link on the same page must not also be marked active
    assert '<a href="/ui/accounts" class="active">' not in resp.text


def test_nav_is_grouped_into_labeled_sections(client, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="nav-sections")
    _login(client, tenant.slug, users["admin"].email)

    resp = client.get("/ui/")
    assert resp.status_code == 200
    for section in ["Positive pay", "ACH", "Review", "Organization"]:
        assert f'class="nav-section">{section}</p>' in resp.text


def test_nav_hides_organization_section_for_role_with_no_org_permissions(client, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="nav-sections-hidden")
    _login(client, tenant.slug, users["viewer"].email)

    resp = client.get("/ui/")
    assert resp.status_code == 200
    # Viewer holds no customer/admin/user/security_group/tenant/audit_log management
    # permission by default, so the whole "Organization" section header must not render
    # empty above nothing.
    assert 'class="nav-section">Organization</p>' not in resp.text
