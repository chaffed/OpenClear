# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Lists that search, filter, sort and export on the server, global search, and the
navigation changes (UX_PLAN.md U12; MASTER_PLAN.md M2; web/list_search.py)."""

import csv
import io
from datetime import date
from decimal import Decimal

import pytest

from sqlalchemy import select

from openclear.domain.audit_log_entry import AuditLogEntry
from openclear.networks.check.ingestion import PaidItemSubmission, ingest_paid_item
from openclear.services import issued_item_service
from tests.conftest import TenantFactory


def _login(client, tenant, email):
    client.get("/ui/login")
    csrf = client.cookies.get("csrf_token")
    client.post("/ui/login", data={"tenant_slug": tenant.slug, "email": email, "password": TenantFactory.PASSWORD,
                                   "csrf_token": csrf, "next": "/ui/"})
    return csrf


def _issue(db_session, tenant, account, users, number, amount, payee, day=1):
    issued_item_service.create_issued_item(db_session, tenant.id, issued_item_service.IssuedItemInput(
        account_id=account.id, check_number=number, amount=Decimal(amount), payee_name=payee, issue_date=date(2026, 1, day),
    ), submitted_by_user_id=users["preparer"].id)


def _many_checks(db_session, tenant, account, users):
    """60 checks: more than one page, with the one we look for on page 2."""
    for n in range(60):
        _issue(db_session, tenant, account, users, f"{1000 + n}", f"{10 + n}.00", "Acme Supplies" if n % 2 else "Beta Corp", day=1 + n % 28)
    db_session.commit()


@pytest.mark.asvs("14.2.1")
def test_search_finds_items_on_any_page(client, db_session, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="list-search")
    _many_checks(db_session, tenant, account, users)
    _login(client, tenant, users["admin"].email)

    first_page = client.get("/ui/issued-items?sort=number&dir=desc").text
    assert ">1000</a>" not in first_page and ">1059</a>" in first_page  # check 1000 is on another page
    found = client.get("/ui/issued-items?q=1000").text
    assert ">1000</a>" in found and ">1001</a>" not in found and "Showing 1–1 of 1" in found
    assert "Showing 1–30 of 30" in client.get("/ui/issued-items?q=acme").text  # payee, part of a name
    assert ">1012</a>" in client.get("/ui/issued-items?q=22.00").text  # an amount
    ranged = client.get("/ui/issued-items?amount_min=20&amount_max=24.50").text
    assert "Showing 1–5 of 5" in ranged
    # An account number is posted and becomes the account's id in the URL (ASVS 14.2.1).
    posted = client.post("/ui/list-search", data={"list": "/ui/issued-items", "account": account.account_number,
                                                  "status": "outstanding", "csrf_token": client.cookies.get("csrf_token")},
                         follow_redirects=False)
    assert account.account_number not in posted.headers["location"] and f"account_id={account.id}" in posted.headers["location"]
    by_account = client.get(posted.headers["location"]).text
    assert f'name="csrf_token" value="{client.cookies.get("csrf_token")}"' in by_account  # the form can post again
    assert "of 60" in by_account and f'value="{account.account_number}"' in by_account
    assert "Nothing matches" in client.get("/ui/issued-items?account_id=none").text
    nothing = client.get("/ui/issued-items?q=nope-not-here").text
    assert "Nothing matches these filters" in nothing

    # Sorting is across every row, and pages keep the search.
    ascending = client.get("/ui/issued-items?sort=amount&dir=asc").text
    assert ascending.index("10.00") < ascending.index("11.00") and 'aria-sort="ascending"' in ascending
    page_two = client.get("/ui/issued-items?q=acme&page=2").text
    assert "Showing 1–30 of 30" in page_two  # only one page of acme: clamped, still searching


def test_export_matches_the_filters_and_is_logged(client, db_session, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="list-export")
    _many_checks(db_session, tenant, account, users)
    _issue(db_session, tenant, account, users, "9999", "5.00", "=HYPERLINK(\"http://x\")")
    db_session.commit()
    _login(client, tenant, users["admin"].email)
    response = client.get("/ui/issued-items?q=acme&export=csv")
    assert response.status_code == 200 and response.headers["content-type"].startswith("text/csv")
    rows = list(csv.DictReader(io.StringIO(response.text)))
    assert len(rows) == 30 and all(r["payee"] == "Acme Supplies" for r in rows)
    injected = list(csv.DictReader(io.StringIO(client.get("/ui/issued-items?number=9999&export=csv").text)))
    assert injected[0]["payee"].startswith("'=")  # never a formula in a spreadsheet
    entry = db_session.execute(select(AuditLogEntry).where(AuditLogEntry.tenant_id == tenant.id,
                                                           AuditLogEntry.action == "list.export")).scalars().first()
    assert entry.metadata_json["rows"] == 30 and entry.metadata_json["filters"] == {"q": "acme"}


def test_exceptions_are_searched_through_their_item(client, db_session, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="list-exceptions")
    for number, amount in (("5001", "120.00"), ("5002", "75.00")):
        ingest_paid_item(db_session, tenant.id, PaidItemSubmission(account_id=account.id, check_number=number,
                                                                   presented_amount=Decimal(amount), presented_date=date(2026, 3, 2)))
    db_session.commit()
    _login(client, tenant, users["admin"].email)
    page = client.get("/ui/exceptions?number=5002").text
    assert "Check #5002" in page and "Check #5001" not in page
    assert "Check #5001" in client.get("/ui/exceptions?q=120.00").text
    detail_link = next(part.split('"')[0] for part in page.split('href="/ui/exceptions/')[1:] if "?from=" in part.split('"')[0])
    detail = client.get(f"/ui/exceptions/{detail_link.replace('&amp;', '&')}").text
    assert 'href="/ui/exceptions?number=5002"' in detail  # back to the filtered list
    assert "<title>Check #5002 · $75.00 · Exception" in detail
    # A crafted "from" can't send anyone elsewhere.
    exception_id = detail_link.split("?")[0]
    assert 'href="/ui/exceptions"' in client.get(f"/ui/exceptions/{exception_id}?from=https://evil.example/").text


def test_global_search_and_navigation(client, db_session, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="list-global")
    _issue(db_session, tenant, account, users, "7777", "300.00", "Gamma LLC")
    ingest_paid_item(db_session, tenant.id, PaidItemSubmission(account_id=account.id, check_number="7777",
                                                               presented_amount=Decimal("3000.00"), presented_date=date(2026, 3, 2)))
    db_session.commit()
    _login(client, tenant, users["admin"].email)
    results = client.get("/ui/search?q=7777").text
    for section in ("Exceptions", "Issued checks", "Presented checks"):
        assert f"<h2>{section}" in results
    assert "Nothing found" in client.get("/ui/search?q=zzz-none").text

    dashboard = client.get("/ui/").text
    nav = dashboard.split('class="nav-links"')[1]
    assert nav.index("Exceptions") < nav.index("Issued Items")  # the work first
    assert 'class="nav-count" aria-label="1 waiting">1<' in dashboard  # one exception needs attention
    assert "Action Log" in dashboard and "Audit Log" not in dashboard


def test_page_only_filters_say_so(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="list-pager")
    _login(client, tenant, users["admin"].email)
    page = client.get("/ui/users").text
    assert 'class="pagination" data-pages="' in page
