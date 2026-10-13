# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import re

from tests.conftest import TenantFactory


def _login(client, tenant_slug, email, password=TenantFactory.PASSWORD):
    client.get("/ui/login")
    csrf = client.cookies.get("csrf_token")
    return client.post(
        "/ui/login", data={"tenant_slug": tenant_slug, "email": email, "password": password, "csrf_token": csrf, "next": "/ui/"}
    )


def test_create_account_and_issued_item_via_web(client, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="web-issued-items")
    _login(client, tenant.slug, users["admin"].email)

    accounts_page = client.get("/ui/accounts")
    assert accounts_page.status_code == 200
    assert account.account_number in accounts_page.text

    csrf = client.cookies.get("csrf_token")
    created = client.post(
        "/ui/accounts", data={"account_number": "999", "name": "Payroll", "csrf_token": csrf}, follow_redirects=False
    )
    assert created.status_code == 303

    new_form = client.get("/ui/issued-items/new")
    assert new_form.status_code == 200
    assert "Payroll" in new_form.text

    resp = client.post(
        "/ui/issued-items",
        data={
            "account_id": str(account.id),
            "check_number": "5001",
            "amount": "150.00",
            "payee_name": "Vendor X",
            "issue_date": "2026-01-01",
            "csrf_token": csrf,
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303

    list_page = client.get("/ui/issued-items")
    assert "5001" in list_page.text

    match = re.search(r"/ui/issued-items/([0-9a-f-]{36})", list_page.text)
    assert match is not None, list_page.text
    item_id = match.group(1)

    detail = client.get(f"/ui/issued-items/{item_id}")
    assert detail.status_code == 200
    assert "outstanding" in detail.text

    void_resp = client.post(f"/ui/issued-items/{item_id}/void", data={"reason": "test", "csrf_token": csrf}, follow_redirects=False)
    assert void_resp.status_code == 303

    detail_after = client.get(f"/ui/issued-items/{item_id}")
    assert "voided" in detail_after.text


def test_viewer_cannot_create_issued_item(client, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="web-viewer-forbidden")
    _login(client, tenant.slug, users["viewer"].email)

    resp = client.get("/ui/issued-items/new", follow_redirects=False)
    assert resp.status_code == 403


def test_issued_item_detail_404_for_unknown_id(client, tenant_factory):
    import uuid

    tenant, _account, users = tenant_factory.make(slug="web-issued-item-404")
    _login(client, tenant.slug, users["admin"].email)

    resp = client.get(f"/ui/issued-items/{uuid.uuid4()}")
    assert resp.status_code == 404


def test_issued_items_list_paginates(client, db_session, tenant_factory):
    from datetime import date
    from decimal import Decimal

    from openclear.services import issued_item_service

    tenant, account, users = tenant_factory.make(slug="web-issued-items-pagination")
    for i in range(60):
        issued_item_service.create_issued_item(
            db_session, tenant.id,
            issued_item_service.IssuedItemInput(
                account_id=account.id, check_number=str(9000 + i), amount=Decimal("10.00"), payee_name="V",
                issue_date=date(2026, 1, 1),
            ),
            submitted_by_user_id=users["admin"].id,
        )
    db_session.commit()
    _login(client, tenant.slug, users["admin"].email)

    page1 = client.get("/ui/issued-items")
    assert page1.status_code == 200
    assert "Showing 1" in page1.text
    assert "of 60" in page1.text

    page2 = client.get("/ui/issued-items?page=2")
    assert page2.status_code == 200
    assert "Showing 51" in page2.text

    # page=99 is clamped to the actual last page (2, at 50/page for 60 rows) rather than
    # showing an empty page or erroring
    out_of_range = client.get("/ui/issued-items?page=99")
    assert out_of_range.status_code == 200
    assert "Showing 51" in out_of_range.text

    # Made in the same second, the checks still keep one order from page to page: every one
    # is on exactly one page.
    import re

    shown = re.findall(r">(9\d{3})</a>", page1.text) + re.findall(r">(9\d{3})</a>", page2.text)
    assert sorted(shown) == [str(9000 + i) for i in range(60)]
