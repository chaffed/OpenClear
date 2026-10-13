# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Deciding on a phone and a tablet (MOBILE_PLAN.md R2): the exception queue as cards, its
filters in a bottom sheet. The browser tests use Playwright's Chromium and are skipped
where it isn't installed."""

from openclear.domain.exception_item import ExceptionItem
from tests.test_ml.test_per_customer_ml import _make_exception
from tests.test_web.test_mobile_components import _sign_in, browser_page  # noqa: F401 -- a fixture


def test_the_queue_is_cards_with_checkboxes_on_a_phone(client, db_session, tenant_factory, browser_page):  # noqa: F811
    tenant, account, users = tenant_factory.make(slug="queue-cards")
    for number in ("8601", "8602"):
        _make_exception(db_session, tenant, account, users, number, "10.00", "99.00")
    _sign_in(client, tenant, users)

    page = browser_page(360)
    page.goto("http://testserver/ui/exceptions")
    card = page.locator("table.responsive tbody tr").first
    assert page.evaluate("getComputedStyle(document.querySelector('table.responsive tbody tr')).display") == "grid"
    box = card.locator("label.select-target").bounding_box()
    title, money = card.locator("td.cell-primary").bounding_box(), card.locator("td.cell-money").bounding_box()
    assert box["width"] >= 44 and box["height"] >= 44
    assert box["x"] + box["width"] <= title["x"] + 1 and money["x"] > title["x"]  # left of the title, clear of the amount

    # Ticking items shows the bulk bar, stuck to the bottom of the screen.
    bar = page.locator("[data-bulk-bar]")
    assert not bar.is_visible()
    card.locator("label.select-target").click()
    page.locator("table.responsive tbody tr").nth(1).locator("label.select-target").click()
    assert bar.is_visible() and "2 selected" in bar.inner_text()
    assert bar.bounding_box()["y"] + bar.bounding_box()["height"] <= 800 + 1

    # Return's reasons are a sheet, still part of the bulk form: returning both works.
    assert not page.locator("details.bulk-return > summary").is_visible()
    page.locator("[data-sheet-open='bulk-return']").click()
    sheet = page.get_by_role("dialog", name="Return the selected items")
    sheet.locator("select[name='check_return_reason_id']").select_option(label="Stop payment")
    sheet.get_by_role("button", name="Return selected").click()
    # (The test's server doesn't follow the redirect into the browser, so wait for the answer.)
    with page.expect_response("**/ui/exceptions/bulk") as answer:
        page.locator("dialog.confirm-dialog").get_by_role("button", name="Return 2").click()
    assert "flash=Returned%202%20items" in answer.value.headers["location"]
    db_session.expire_all()
    assert sorted(i.status.value for i in db_session.query(ExceptionItem).filter_by(tenant_id=tenant.id)) == ["return", "return"]

    # Network and status are in a sheet; the button says what's chosen.
    page = browser_page(360)
    page.goto("http://testserver/ui/exceptions?status=all")
    trigger = page.locator("[data-sheet-open='queue-filters']")
    assert trigger.inner_text().strip() == "All statuses · Checks and ACH"
    assert not page.locator("select[name='network_code']").is_visible()
    trigger.click()
    sheet = page.get_by_role("dialog", name="Network and status")
    sheet.locator("select[name='network_code']").select_option("check")
    page.wait_for_url("**network_code=check**")
    assert page.locator("[data-sheet-open='queue-filters']").inner_text().strip() == "All statuses · Checks"


def test_the_queue_on_a_desktop_is_a_table_with_row_keys(client, db_session, tenant_factory, browser_page):  # noqa: F811
    tenant, account, users = tenant_factory.make(slug="queue-desk")
    for number in ("8701", "8702"):
        _make_exception(db_session, tenant, account, users, number, "10.00", "99.00")
    _sign_in(client, tenant, users)

    page = browser_page(1280)
    page.goto("http://testserver/ui/exceptions")
    assert page.locator("select[name='status']").is_visible()
    assert not page.locator("[data-sheet-open='queue-filters']").is_visible()
    assert page.locator("table.responsive thead th").count() == 7
    page.keyboard.press("j")
    page.keyboard.press("j")
    assert page.locator("tr.row-focused").count() == 1
    assert page.evaluate("document.activeElement.textContent").startswith("Check #87")
    page.locator("thead [data-select-all]").check()
    assert page.locator("[data-bulk-bar]").is_visible() and "2 selected" in page.locator("[data-bulk-count]").inner_text()
