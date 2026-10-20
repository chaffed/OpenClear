# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed
"""The user journeys walked in a real browser, as UI_PLAN.md's review did: each step leads to the
next without the person finding their own way (UI_PLAN.md UI2). Playwright's Chromium, skipped
where it isn't installed."""

import pytest
from sqlalchemy import select

from openclear.domain.account import Account
from openclear.domain.customer import Customer
from openclear.domain.exception_item import ExceptionItem
from openclear.domain.stop_payment import StopPayment
from openclear.services import security_group_service, user_service
from tests.conftest import TenantFactory
from tests.test_ml.test_per_customer_ml import _make_customer_with_account, _make_exception
from tests.test_web.test_mobile_components import _sign_in

URL = "http://testserver"


@pytest.fixture
def browser_page(client):
    """As test_mobile_components.browser_page, except every redirect is followed here: Chromium
    won't take a redirect from an intercepted response after a form is posted, and these
    journeys post forms. The page then shows where the form led, under the address it was
    posted to, so the tests read the page, not the address."""
    playwright = pytest.importorskip("playwright.sync_api")
    try:
        runner = playwright.sync_playwright().start()
        browser = runner.chromium.launch()
    except Exception as exc:  # noqa: BLE001 -- no browser installed here
        pytest.skip(f"Chromium isn't available: {exc}")

    def serve(route):
        request = route.request
        response = client.request(request.method, request.url.replace(URL, ""),
                                  headers={k: v for k, v in request.headers.items() if k.lower() != "cookie"},
                                  content=request.post_data_buffer, follow_redirects=True)
        route.fulfill(status=response.status_code, headers=dict(response.headers), body=response.content)

    errors: list[str] = []

    def open_at(width: int, height: int = 800):
        context = browser.new_context(viewport={"width": width, "height": height}, reduced_motion="reduce", bypass_csp=True)
        context.route(URL + "/**", serve)
        page = context.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))
        return page

    yield open_at
    browser.close()
    runner.stop()
    assert not errors, errors


def _customer_user(db_session, tenant, slug: str):
    customer, account = _make_customer_with_account(db_session, tenant, slug.upper())
    group = security_group_service.get_security_group_by_name(db_session, tenant.id, "Admin")
    user = user_service.create_user_with_membership(db_session, tenant.id, email=f"ap@{slug}-customer.example.com",
                                                    password=TenantFactory.PASSWORD, security_group_id=group.id, customer_id=customer.id)
    db_session.commit()
    return customer, account, user


def _sign_in_as(client, tenant, email: str):
    client.cookies.clear()
    client.get("/ui/login")
    csrf = client.cookies.get("csrf_token")
    client.post("/ui/login", data={"tenant_slug": tenant.slug, "email": email, "password": TenantFactory.PASSWORD,
                                   "csrf_token": csrf, "next": "/ui/"})


def test_a_customer_decides_one_item_after_another(client, db_session, tenant_factory, browser_page):  # noqa: F811
    tenant, _account, users = tenant_factory.make(slug="journey-decide")
    _customer, account, user = _customer_user(db_session, tenant, "jd")
    for number in ("7001", "7002", "7003"):
        _make_exception(db_session, tenant, account, users, number, "10.00", "99.00")
    _sign_in_as(client, tenant, user.email)
    page = browser_page(1280, 900)

    page.goto(URL + "/ui/")
    assert page.locator("h1", has_text="Exceptions").count() == 1  # items to decide: they land on them
    page.locator("td.cell-primary a").first.click()
    seen = []
    for _ in range(3):
        page.wait_for_selector("button.button-pay")
        seen.append(page.locator("h1").first.inner_text())
        page.locator("button.button-pay").click()
        page.wait_for_selector(".toast")
        assert "paid." in page.locator(".toast").inner_text()
    # Each Pay moved on to another item; after the last, back to the list, which says so.
    assert len(set(seen)) == 3
    assert page.locator("h1", has_text="Exceptions").count() == 1
    assert "Nothing else is waiting" in page.locator(".toast").inner_text()
    db_session.expire_all()
    assert {i.status.value for i in db_session.execute(select(ExceptionItem)).scalars()} == {"pay"}


def test_decide_now_is_never_covered_and_home_reaches_the_dashboard(client, db_session, tenant_factory, browser_page):  # noqa: F811
    tenant, _account, users = tenant_factory.make(slug="journey-banner")
    _customer, account, user = _customer_user(db_session, tenant, "jb")
    _make_exception(db_session, tenant, account, users, "7101", "10.00", "99.00")
    _sign_in_as(client, tenant, user.email)

    for width in (1280, 390):
        page = browser_page(width, 900)
        page.goto(URL + "/ui/stop-payments")
        link = page.locator(".decide-banner a")
        box = link.bounding_box()
        on_top = page.evaluate("([x, y]) => document.elementFromPoint(x, y).closest('a')?.textContent",
                               [box["x"] + box["width"] / 2, box["y"] + box["height"] / 2])
        assert on_top == "Decide now", f"at {width}px the link is covered"

    page = browser_page(1280, 900)
    page.goto(URL + "/ui/exceptions")
    page.locator(".sidenav a", has_text="Home").click()
    page.wait_for_selector(".jobs h2:has-text('to decide')")  # the customer's dashboard leads with its jobs


def test_a_stop_payment_needs_only_the_check_number(client, db_session, tenant_factory, browser_page):  # noqa: F811
    tenant, _account, users = tenant_factory.make(slug="journey-stop")
    _customer, _cust_account, user = _customer_user(db_session, tenant, "js")
    _sign_in_as(client, tenant, user.email)
    page = browser_page(1280, 900)

    page.goto(URL + "/ui/stop-payments/new")
    page.fill("input[name=check_number]", "8003")
    page.locator("#main-content form button[type=submit]").click()
    page.wait_for_selector(".toast:has-text('Stop payment created.')")
    db_session.expire_all()
    stop = db_session.execute(select(StopPayment).where(StopPayment.check_number == "8003")).scalar_one()
    assert stop.effective_date is not None


def test_the_customer_checklist_brings_the_bank_back(client, db_session, tenant_factory, browser_page):  # noqa: F811
    tenant, _account, users = tenant_factory.make(slug="journey-onboard")
    _sign_in(client, tenant, users)
    page = browser_page(1280, 900)

    page.goto(URL + "/ui/customers/new")
    page.fill("input[name=customer_number]", "CUST-77")
    page.fill("input[name=name]", "Harbor Freight Co")
    page.locator("#main-content form button[type=submit]").click()
    page.wait_for_selector("h1:has-text('Getting Started')")
    assert page.locator(".checklist-state", has_text="To do").count() == 2

    page.locator("a", has_text="Add an account").click()
    page.wait_for_selector("input[name=account_number]")
    customer = db_session.execute(select(Customer).where(Customer.customer_number == "CUST-77")).scalar_one()
    assert page.locator("select[name=customer_id]").input_value() == str(customer.id)  # already chosen
    page.fill("input[name=account_number]", "555000111")
    page.fill("input[name=name]", "Harbor Operating")
    page.locator("#main-content form button[type=submit]").click()
    page.wait_for_selector("h1:has-text('Getting Started — Harbor Freight Co')")  # back on the checklist
    assert page.locator(".checklist-state", has_text="Done").count() == 1
    db_session.expire_all()
    assert db_session.execute(select(Account).where(Account.account_number == "555000111")).scalar_one().customer_id == customer.id


def test_an_upload_says_what_happened_then_how_to_fix_it(client, db_session, tenant_factory, browser_page):  # noqa: F811
    tenant, account, users = tenant_factory.make(slug="journey-upload")
    csrf = _sign_in(client, tenant, users)
    data = ("account_number,check_number,amount,payee_name,issue_date\n"
            f"{account.account_number},8001,250.00,Acme Vendor,2026-10-15\n"
            f"{account.account_number},8002,12O.00,Bad Amount Co,2026-10-15\n").encode()
    # Uploaded through the client: an intercepted request can't carry a file's body.
    uploaded = client.post("/ui/issued-items/bulk", data={"csrf_token": csrf}, files={"upload_file": ("issued.csv", data, "text/csv")},
                           follow_redirects=False)
    result = uploaded.headers["location"]
    page = browser_page(1280, 900)

    page.goto(URL + result)
    page.wait_for_selector(".upload-outcome")
    outcome = page.locator(".upload-outcome").inner_text()
    assert "1 issued check added." in outcome and "1 row needs fixing." in outcome
    assert "Check 8001 · $250.00 · Acme Vendor" in page.locator("#main-content").inner_text()
    assert not page.locator(".file-details table").is_visible()  # the file's details are folded away

    page.locator("a", has_text="Fix failed rows").click()
    page.fill("input[value='12O.00']", "120.00")
    page.locator("button", has_text="Import fixed rows").click()
    page.wait_for_selector(".upload-outcome")
    assert "Every failed row has been fixed." in page.locator(".upload-outcome").inner_text()
