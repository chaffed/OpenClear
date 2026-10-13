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


def test_the_decision_bar_on_a_phone(client, db_session, tenant_factory, browser_page):  # noqa: F811
    tenant, account, users = tenant_factory.make(slug="decision-bar")
    item = _make_exception(db_session, tenant, account, users, "8901", "10.00", "99.00")
    item.ml_score = 0.2  # an 80% chance it should be returned: High
    db_session.commit()
    _sign_in(client, tenant, users)

    page = browser_page(1280)
    page.goto(f"http://testserver/ui/exceptions/{item.id}")
    assert not page.locator(".decision-score").is_visible()
    assert page.locator(".decision-return > summary").is_visible()
    assert not page.locator("[data-sheet-open='return-decide']").is_visible()

    page = browser_page(360, 780)
    page.goto(f"http://testserver/ui/exceptions/{item.id}")
    bar = page.locator(".decision-card").bounding_box()
    assert bar["y"] + bar["height"] <= 780 + 1 and bar["height"] < 160  # stuck to the bottom, and small
    score = page.locator(".decision-score")
    pay = page.get_by_role("button", name="Pay", exact=True)
    ret = page.locator("[data-sheet-open='return-decide']")
    assert "High · 80%" in score.inner_text()
    boxes = [score.bounding_box(), pay.bounding_box(), ret.bounding_box()]
    assert boxes[0]["x"] < boxes[1]["x"] < boxes[2]["x"] and abs(boxes[1]["y"] - boxes[2]["y"]) < 2  # one row
    assert all(b["height"] >= 44 for b in boxes[1:])

    # R opens the reasons in a sheet, the first one focused; returning from it works.
    page.keyboard.press("r")
    sheet = page.get_by_role("dialog", name="Why is it being returned?")
    assert sheet.is_visible()
    assert page.evaluate("document.activeElement.name") == "return_reason_id"
    sheet.get_by_label("Stop payment").check()
    with page.expect_response(f"**/ui/exceptions/{item.id}/decide") as answer:
        sheet.get_by_role("button", name="Return it").click()
    assert "Decision+recorded" in answer.value.headers["location"]
    db_session.expire_all()
    assert db_session.get(ExceptionItem, item.id).status.value == "return"


def test_pay_on_a_phone_asks_first_when_the_risk_is_high(client, db_session, tenant_factory, browser_page):  # noqa: F811
    tenant, account, users = tenant_factory.make(slug="decision-confirm")
    item = _make_exception(db_session, tenant, account, users, "8902", "10.00", "99.00")
    item.ml_score = 0.2
    db_session.commit()
    _sign_in(client, tenant, users)

    page = browser_page(360, 780)
    page.goto(f"http://testserver/ui/exceptions/{item.id}")
    page.get_by_role("button", name="Pay", exact=True).click()
    confirm = page.locator("dialog.confirm-dialog")
    assert confirm.is_visible()
    box = confirm.bounding_box()
    assert box["x"] >= 0 and box["x"] + box["width"] <= 360
    for name in ("Cancel", "Pay it"):
        assert confirm.get_by_role("button", name=name).bounding_box()["height"] >= 44
    confirm.get_by_role("button", name="Cancel").click()
    db_session.expire_all()
    assert db_session.get(ExceptionItem, item.id).status.value == "open"
