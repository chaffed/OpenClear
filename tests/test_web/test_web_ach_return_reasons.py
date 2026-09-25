# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

from openclear.services import ach_return_reason_service
from tests.conftest import TenantFactory


def _login(client, tenant_slug, email, password=TenantFactory.PASSWORD):
    client.get("/ui/login")
    csrf = client.cookies.get("csrf_token")
    client.post(
        "/ui/login", data={"tenant_slug": tenant_slug, "email": email, "password": password, "csrf_token": csrf, "next": "/ui/"}
    )
    return csrf


def test_ach_return_reasons_page_requires_permission(client, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="web-arr-forbidden")
    _login(client, tenant.slug, users["viewer"].email)

    resp = client.get("/ui/ach-return-reasons", follow_redirects=False)
    assert resp.status_code == 403


def test_ach_return_reasons_page_lists_seeded_defaults(client, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="web-arr-list")
    _login(client, tenant.slug, users["admin"].email)

    resp = client.get("/ui/ach-return-reasons")
    assert resp.status_code == 200
    assert "Insufficient Funds" in resp.text


def test_create_ach_return_reason(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="web-arr-create")
    csrf = _login(client, tenant.slug, users["admin"].email)

    resp = client.post(
        "/ui/ach-return-reasons",
        data={"csrf_token": csrf, "reason_text": "Duplicate Entry", "transaction_code": "667"},
        follow_redirects=False,
    )
    assert resp.status_code == 303

    reasons = ach_return_reason_service.list_ach_return_reasons(db_session, tenant.id)
    created = next(r for r in reasons if r.reason_text == "Duplicate Entry")
    assert created.transaction_code == "667"


def test_create_ach_return_reason_rejects_bad_transaction_code(client, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="web-arr-bad-code")
    csrf = _login(client, tenant.slug, users["admin"].email)

    resp = client.post(
        "/ui/ach-return-reasons",
        data={"csrf_token": csrf, "reason_text": "Some Reason", "transaction_code": "abc"},
    )
    assert resp.status_code == 422


def test_edit_ach_return_reason(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="web-arr-edit")
    csrf = _login(client, tenant.slug, users["admin"].email)
    reason = ach_return_reason_service.list_ach_return_reasons(db_session, tenant.id)[0]

    resp = client.post(
        f"/ui/ach-return-reasons/{reason.id}",
        data={"csrf_token": csrf, "reason_text": "Renamed Reason", "transaction_code": "42"},
        follow_redirects=False,
    )
    assert resp.status_code == 303

    db_session.expire_all()
    updated = ach_return_reason_service.get_ach_return_reason(db_session, tenant.id, reason.id)
    assert updated.reason_text == "Renamed Reason"
    assert updated.transaction_code == "42"


def test_deactivate_and_reactivate_ach_return_reason(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="web-arr-deactivate")
    csrf = _login(client, tenant.slug, users["admin"].email)
    reason = ach_return_reason_service.list_ach_return_reasons(db_session, tenant.id)[0]

    resp = client.post(f"/ui/ach-return-reasons/{reason.id}/deactivate", data={"csrf_token": csrf}, follow_redirects=False)
    assert resp.status_code == 303

    list_page = client.get("/ui/ach-return-reasons")
    assert "deactivated" in list_page.text

    resp = client.post(f"/ui/ach-return-reasons/{reason.id}/reactivate", data={"csrf_token": csrf}, follow_redirects=False)
    assert resp.status_code == 303
