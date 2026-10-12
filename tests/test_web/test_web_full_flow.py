# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import re

from tests.conftest import TenantFactory
from tests.test_ocr.conftest import make_check_image


def _login(client, tenant_slug, email, password=TenantFactory.PASSWORD):
    client.get("/ui/login")
    csrf = client.cookies.get("csrf_token")
    return client.post(
        "/ui/login", data={"tenant_slug": tenant_slug, "email": email, "password": password, "csrf_token": csrf, "next": "/ui/"}
    )


def _extract_uuid(html: str, path_prefix: str) -> str:
    match = re.search(re.escape(path_prefix) + r"([0-9a-f-]{36})", html)
    assert match is not None, html
    return match.group(1)


def test_stop_payment_blocks_matching_paid_item_via_web(client, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="web-flow-stop")
    _login(client, tenant.slug, users["admin"].email)
    csrf = client.cookies.get("csrf_token")

    client.post(
        "/ui/issued-items",
        data={
            "account_id": str(account.id), "check_number": "7001", "amount": "100.00",
            "payee_name": "Vendor", "issue_date": "2026-01-01", "csrf_token": csrf,
        },
    )
    client.post(
        "/ui/stop-payments",
        data={"account_id": str(account.id), "check_number": "7001", "effective_date": "2026-01-01", "csrf_token": csrf},
    )

    stops_page = client.get("/ui/stop-payments")
    assert "active" in stops_page.text

    paid_resp = client.post(
        "/ui/paid-items",
        data={
            "account_id": str(account.id), "check_number": "7001", "presented_amount": "100.00",
            "presented_date": "2026-01-10", "csrf_token": csrf,
        },
        follow_redirects=False,
    )
    assert paid_resp.status_code == 303
    detail = client.get(paid_resp.headers["location"])
    assert "exception" in detail.text


def test_check_image_upload_and_ocr_via_web(client, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="web-flow-check-image")
    _login(client, tenant.slug, users["admin"].email)
    csrf = client.cookies.get("csrf_token")

    image_bytes = make_check_image(payee="Web UI Vendor", amount="42.00")
    upload_resp = client.post(
        "/ui/check-images",
        data={"paid_item_id": "", "csrf_token": csrf},
        files={"front_image": ("check.png", image_bytes, "image/png")},
        follow_redirects=False,
    )
    assert upload_resp.status_code == 303

    detail = client.get(upload_resp.headers["location"])
    assert detail.status_code == 200
    assert "Web UI Vendor" in detail.text  # background task ran synchronously under TestClient
    assert "completed" in detail.text


def test_ach_authorization_and_transaction_via_web(client, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="web-flow-ach")
    _login(client, tenant.slug, users["admin"].email)
    csrf = client.cookies.get("csrf_token")

    client.post(
        "/ui/ach/authorizations",
        data={
            "account_id": str(account.id), "originator_id": "PAY1", "originator_name": "Payroll Co",
            "receiver_id": "", "max_amount": "500.00", "frequency_limit": "", "allowed_sec_codes": "",
            "effective_date": "2026-01-01", "expiration_date": "", "csrf_token": csrf,
        },
    )
    auths_page = client.get("/ui/ach/authorizations")
    assert "Payroll Co" in auths_page.text

    txn_resp = client.post(
        "/ui/ach/transactions",
        data={
            "account_id": str(account.id), "originator_id": "UNKNOWN", "originator_name": "Suspicious LLC",
            "receiver_id": "", "amount": "10.00", "transaction_type": "debit", "sec_code": "WEB",
            "trace_number": "T1", "effective_date": "2026-01-10", "csrf_token": csrf,
        },
        follow_redirects=False,
    )
    assert txn_resp.status_code == 303
    detail = client.get(txn_resp.headers["location"])
    assert "exception" in detail.text


def test_full_exception_review_flow_via_web(client, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="web-flow-exceptions", require_dual_control=False)
    _login(client, tenant.slug, users["admin"].email)
    csrf = client.cookies.get("csrf_token")

    client.post(
        "/ui/issued-items",
        data={
            "account_id": str(account.id), "check_number": "8001", "amount": "100.00",
            "payee_name": "Vendor", "issue_date": "2026-01-01", "csrf_token": csrf,
        },
    )
    client.post(
        "/ui/paid-items",
        data={
            "account_id": str(account.id), "check_number": "8001", "presented_amount": "999.00",
            "presented_date": "2026-01-10", "csrf_token": csrf,
        },
    )

    queue = client.get("/ui/exceptions")
    assert queue.status_code == 200
    exception_id = _extract_uuid(queue.text, "/ui/exceptions/")

    detail = client.get(f"/ui/exceptions/{exception_id}")
    assert "Amount mismatch" in detail.text  # shown in plain English, not the rule code
    assert "Not scored yet" in detail.text  # cold start, no model trained

    decide_resp = client.post(
        f"/ui/exceptions/{exception_id}/decide",
        data={"outcome": "return", "reason_code": "confirmed_fraud", "notes": "test", "csrf_token": csrf},
        follow_redirects=False,
    )
    assert decide_resp.status_code == 303
    assert "error" not in decide_resp.headers["location"]

    final_detail = client.get(f"/ui/exceptions/{exception_id}")
    assert "return" in final_detail.text


def test_admin_ml_page_requires_admin_permission(client, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="web-flow-admin-forbidden")
    _login(client, tenant.slug, users["preparer"].email)

    resp = client.get("/ui/admin", follow_redirects=False)
    assert resp.status_code == 403


def test_admin_ml_page_lists_networks(client, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="web-flow-admin-ok")
    _login(client, tenant.slug, users["admin"].email)

    resp = client.get("/ui/admin/ml-models")
    assert resp.status_code == 200
    assert "check" in resp.text
    assert "ach" in resp.text


def _make_single_permission_user(db_session, tenant, *, permission: str, email: str):
    from openclear.services import security_group_service, user_service
    from openclear.services.security_group_service import SecurityGroupInput

    group = security_group_service.create_security_group(
        db_session, tenant.id, SecurityGroupInput(name=f"Only {permission}", permissions=[permission])
    )
    user = user_service.create_user_with_membership(
        db_session, tenant.id, email=email, password=TenantFactory.PASSWORD, security_group_id=group.id,
    )
    db_session.commit()
    return user


def test_admin_page_shared_by_tenant_manage_and_admin_manage_each_seeing_only_their_own_section(client, db_session, tenant_factory):
    tenant, _account, _users = tenant_factory.make(slug="web-flow-admin-shared")
    tenant_only = _make_single_permission_user(db_session, tenant, permission="tenant:manage", email="tenantonly@example.com")
    admin_only = _make_single_permission_user(db_session, tenant, permission="admin:manage", email="adminonly@example.com")

    # /ui/admin is the control panel now; each sees only the tiles their permission covers.
    _login(client, tenant.slug, tenant_only.email)
    tenant_only_page = client.get("/ui/admin")
    assert tenant_only_page.status_code == 200
    assert 'data-key="sso"' in tenant_only_page.text
    assert 'data-key="ml-models"' not in tenant_only_page.text

    client.cookies.clear()
    _login(client, tenant.slug, admin_only.email)
    admin_only_page = client.get("/ui/admin")
    assert admin_only_page.status_code == 200
    assert 'data-key="ml-models"' in admin_only_page.text
    assert 'data-key="sso"' not in admin_only_page.text


def test_admin_nav_link_visible_to_tenant_manage_without_admin_manage(client, db_session, tenant_factory):
    tenant, _account, _users = tenant_factory.make(slug="web-flow-admin-nav")
    tenant_only = _make_single_permission_user(db_session, tenant, permission="tenant:manage", email="navonly@example.com")

    _login(client, tenant.slug, tenant_only.email)
    dashboard = client.get("/ui/")
    assert 'href="/ui/settings"' in dashboard.text


def test_admin_page_still_403s_for_neither_permission(client, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="web-flow-admin-neither")
    _login(client, tenant.slug, users["viewer"].email)

    resp = client.get("/ui/admin", follow_redirects=False)
    assert resp.status_code == 403
