# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import re

from openclear.services import ach_return_reason_service
from tests.conftest import TenantFactory


def _login(client, tenant_slug, email, password=TenantFactory.PASSWORD):
    client.get("/ui/login")
    csrf = client.cookies.get("csrf_token")
    client.post(
        "/ui/login", data={"tenant_slug": tenant_slug, "email": email, "password": password, "csrf_token": csrf, "next": "/ui/"}
    )
    return csrf


def _create_ach_exception(client, csrf, account):
    client.post(
        "/ui/ach/transactions",
        data={
            "account_id": str(account.id),
            "originator_id": "UNKNOWN01",
            "originator_name": "Suspicious LLC",
            "amount": "75.00",
            "transaction_type": "debit",
            "sec_code": "WEB",
            "trace_number": "TRACE0001",
            "effective_date": "2026-01-10",
            "csrf_token": csrf,
        },
    )
    queue = client.get("/ui/exceptions")
    match = re.search(r"/ui/exceptions/([0-9a-f-]{36})", queue.text)
    return match.group(1)


def test_ach_exception_detail_shows_return_reason_select(client, db_session, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="web-exc-ach-select")
    csrf = _login(client, tenant.slug, users["admin"].email)
    exception_id = _create_ach_exception(client, csrf, account)

    detail = client.get(f"/ui/exceptions/{exception_id}")
    assert detail.status_code == 200
    assert 'name="return_reason_id"' in detail.text  # the ACH list, as radio choices
    assert "Insufficient Funds" in detail.text


def test_ach_return_without_selecting_a_reason_is_rejected(client, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="web-exc-ach-no-reason")
    csrf = _login(client, tenant.slug, users["admin"].email)
    exception_id = _create_ach_exception(client, csrf, account)

    resp = client.post(
        f"/ui/exceptions/{exception_id}/decide",
        data={"outcome": "return", "reason_code": "", "notes": "", "csrf_token": csrf},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert "error" in resp.headers["location"]


def test_ach_return_with_selected_reason_succeeds_and_shows_trancode(client, db_session, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="web-exc-ach-with-reason")
    csrf = _login(client, tenant.slug, users["admin"].email)
    exception_id = _create_ach_exception(client, csrf, account)

    reason = ach_return_reason_service.create_ach_return_reason(
        db_session, tenant.id, ach_return_reason_service.AchReturnReasonInput(reason_text="Duplicate Entry", transaction_code="667")
    )
    db_session.commit()

    resp = client.post(
        f"/ui/exceptions/{exception_id}/decide",
        data={"outcome": "return", "reason_code": "", "notes": "", "ach_return_reason_id": str(reason.id), "csrf_token": csrf},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert "error" not in resp.headers["location"]

    detail = client.get(f"/ui/exceptions/{exception_id}")
    assert "Duplicate Entry" in detail.text
    assert "667" in detail.text


def test_check_exception_detail_has_no_return_reason_select(client, tenant_factory):
    from datetime import date
    from decimal import Decimal

    tenant, account, users = tenant_factory.make(slug="web-exc-check-no-select")
    csrf = _login(client, tenant.slug, users["admin"].email)

    client.post(
        "/ui/issued-items",
        data={
            "account_id": str(account.id), "check_number": "1", "amount": "100.00",
            "payee_name": "Vendor", "issue_date": "2026-01-01", "csrf_token": csrf,
        },
    )
    client.post(
        "/ui/paid-items",
        data={
            "account_id": str(account.id), "check_number": "1", "presented_amount": "999.00",
            "presented_date": "2026-01-10", "csrf_token": csrf,
        },
    )
    queue = client.get("/ui/exceptions")
    exception_id = re.search(r"/ui/exceptions/([0-9a-f-]{36})", queue.text).group(1)

    detail = client.get(f"/ui/exceptions/{exception_id}")
    assert "ach_return_reason_id" not in detail.text
