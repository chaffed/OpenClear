# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

from tests.conftest import TenantFactory


def _login(client, tenant_slug, email, password=TenantFactory.PASSWORD):
    client.get("/ui/login")
    csrf = client.cookies.get("csrf_token")
    client.post(
        "/ui/login", data={"tenant_slug": tenant_slug, "email": email, "password": password, "csrf_token": csrf, "next": "/ui/"}
    )
    return csrf


def test_help_modal_renders_on_a_page_that_defines_it(client, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="help-modal-present")
    _login(client, tenant.slug, users["admin"].email)

    resp = client.get("/ui/issued-items")
    assert resp.status_code == 200
    assert 'id="page-help-button"' in resp.text
    assert 'id="page-help-dialog"' in resp.text
    assert "About issued items" in resp.text


def test_help_modal_absent_on_pre_auth_login_page(client):
    resp = client.get("/ui/login")
    assert resp.status_code == 200
    assert "page-help-button" not in resp.text
    assert "page-help-dialog" not in resp.text


def test_help_modal_absent_on_transient_confirm_page(client, db_session, tenant_factory):
    from openclear.services import security_group_service, user_service

    tenant_a, _account_a, users_a = tenant_factory.make(slug="help-modal-confirm-a")
    tenant_b, _account_b, users_b = tenant_factory.make(slug="help-modal-confirm-b")
    csrf = _login(client, tenant_b.slug, users_b["admin"].email)
    group_b = security_group_service.get_security_group_by_name(db_session, tenant_b.id, "Viewer")

    resp = client.post(
        "/ui/users",
        data={"csrf_token": csrf, "email": users_a["preparer"].email, "password": "", "security_group_id": str(group_b.id)},
    )
    assert resp.status_code == 200
    assert "Confirm access" in resp.text
    assert "page-help-button" not in resp.text


def test_widened_security_group_form_still_renders_permission_checkboxes(client, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="help-modal-secgroup-width")
    _login(client, tenant.slug, users["admin"].email)

    resp = client.get("/ui/security-groups/new")
    assert resp.status_code == 200
    assert 'class="card"' in resp.text
    assert "card-narrow" not in resp.text
    assert 'type="checkbox" name="permissions"' in resp.text


def test_every_substantive_page_help_dialog_has_non_empty_title(client, tenant_factory):
    """Spot-checks a sample of pages across different resource areas to catch an
    obviously broken help_title/help_body pairing (e.g. a title override with no body,
    which the base.html mechanism would otherwise silently hide)."""
    tenant, account, users = tenant_factory.make(slug="help-modal-sample")
    _login(client, tenant.slug, users["admin"].email)

    sample_pages = [
        "/ui/issued-items",
        "/ui/stop-payments",
        "/ui/paid-items",
        "/ui/ach/authorizations",
        "/ui/ach/transactions",
        "/ui/check-images",
        "/ui/exceptions",
        "/ui/accounts",
        "/ui/customers",
        "/ui/users",
        "/ui/security-groups",
        "/ui/audit-log",
        "/ui/",
    ]
    for path in sample_pages:
        resp = client.get(path)
        assert resp.status_code == 200, path
        assert 'id="page-help-button"' in resp.text, f"missing help button on {path}"
        assert 'id="page-help-dialog"' in resp.text, f"missing help dialog on {path}"
