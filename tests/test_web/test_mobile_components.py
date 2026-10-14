# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""The phone and tablet foundations (MOBILE_PLAN.md R1): the two breakpoints, defined once;
the navigation drawer; the bottom sheet; and responsive_table's cards. The browser tests
use Playwright's Chromium and are skipped where it isn't installed."""

import re
from pathlib import Path

import pytest

from tests.conftest import TenantFactory

STATIC = Path(__file__).resolve().parents[2] / "src" / "openclear" / "static"
BREAKPOINTS = {"639.98px", "1023.98px"}


def test_media_queries_use_only_the_two_breakpoints():
    css = (STATIC / "css" / "app.css").read_text()
    widths = re.findall(r"@media[^{]*\((min|max)-width:\s*([^)]+)\)", css)
    assert widths, "no width queries found"
    wrong = [f"{kind}-width: {value}" for kind, value in widths if kind != "max" or value.strip() not in BREAKPOINTS]
    assert not wrong, f"Use (max-width: 639.98px) or (max-width: 1023.98px) only: {wrong}"
    assert "--bp-phone-max: 639.98px;" in css and "--bp-tablet-max: 1023.98px;" in css


def test_scripts_ask_breakpoints_js_for_widths():
    for script in (STATIC / "js").glob("*.js"):
        if script.name == "breakpoints.js":
            continue
        text = script.read_text()
        assert not re.search(r"matchMedia\(\s*[\"'`]\((min|max)-width", text), f"{script.name}: use OpenClear.breakpoint()"


def _sign_in(client, tenant, users):
    client.get("/ui/login")
    csrf = client.cookies.get("csrf_token")
    client.post("/ui/login", data={"tenant_slug": tenant.slug, "email": users["admin"].email, "password": TenantFactory.PASSWORD,
                                   "csrf_token": csrf, "next": "/ui/"})
    return csrf


@pytest.fixture
def browser_page(client):
    """Returns open(width, height, **options) -> a Playwright page at that size, served by the
    test client; options go to the browser context (has_touch and is_mobile, for a tablet)."""
    playwright = pytest.importorskip("playwright.sync_api")
    try:
        runner = playwright.sync_playwright().start()
        browser = runner.chromium.launch()
    except Exception as exc:  # noqa: BLE001 -- no browser installed here
        pytest.skip(f"Chromium isn't available: {exc}")

    def serve(route):
        request = route.request
        # A redirect isn't routed back through here, so a script's fetch (the exception pane's
        # forms) gets the page it leads to straight away, as a browser's fetch would.
        response = client.request(request.method, request.url.replace("http://testserver", ""),
                                  headers={k: v for k, v in request.headers.items() if k.lower() != "cookie"},
                                  content=request.post_data_buffer, follow_redirects=request.resource_type == "fetch")
        route.fulfill(status=response.status_code, headers=dict(response.headers), body=response.content)

    errors: list[str] = []

    def open_at(width: int, height: int = 800, **options):
        context = browser.new_context(viewport={"width": width, "height": height}, reduced_motion="reduce", bypass_csp=True, **options)
        context.route("http://testserver/**", serve)
        page = context.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))
        return page

    yield open_at
    browser.close()
    runner.stop()
    assert not errors, errors


def test_the_navigation_drawer(client, tenant_factory, browser_page):
    tenant, _account, users = tenant_factory.make(slug="drawer")
    _sign_in(client, tenant, users)

    page = browser_page(1280)
    page.goto("http://testserver/ui/")
    assert page.locator("#sidenav-panel a[href='/ui/issued-items']").is_visible()
    assert not page.locator("#nav-toggle").is_visible() and not page.locator("[data-nav-close]").is_visible()

    for width in (768, 360):
        page = browser_page(width)
        page.goto("http://testserver/ui/")
        link = page.locator("#sidenav-panel a[href='/ui/issued-items']")
        assert not link.is_visible()
        toggle = page.locator("#nav-toggle")
        assert toggle.bounding_box()["height"] >= 44

        toggle.click()
        assert link.is_visible() and toggle.get_attribute("aria-expanded") == "true"
        assert page.evaluate("document.activeElement.closest('#sidenav-panel') !== null")
        assert page.evaluate("document.getElementById('main-content').inert")
        page.keyboard.press("Escape")
        assert not link.is_visible() and toggle.get_attribute("aria-expanded") == "false"
        assert page.evaluate("document.activeElement.id") == "nav-toggle"
        assert not page.evaluate("document.getElementById('main-content').inert")

        toggle.click()
        page.locator(".nav-backdrop").click(position={"x": width - 10, "y": 400})
        assert not link.is_visible()
        toggle.click()
        page.locator("[data-nav-close]").click()
        assert not link.is_visible()

        toggle.click()
        link.click()
        page.wait_for_url("**/ui/issued-items")


def test_more_filters_is_a_bottom_sheet_on_phones(client, tenant_factory, browser_page):
    tenant, _account, users = tenant_factory.make(slug="sheet")
    _sign_in(client, tenant, users)

    page = browser_page(1280)
    page.goto("http://testserver/ui/issued-items")
    assert page.locator("details.list-search-more > summary").is_visible()
    assert not page.locator("[data-sheet-open='list-search-more']").is_visible()
    assert page.locator("dialog.bottom-sheet").count() == 0

    page = browser_page(360)
    page.goto("http://testserver/ui/issued-items")
    trigger = page.locator("[data-sheet-open='list-search-more']")
    number = page.locator("input[name='number']")
    assert trigger.is_visible() and not number.is_visible()
    assert trigger.bounding_box()["height"] >= 44

    trigger.click()
    sheet = page.locator("dialog.bottom-sheet")
    assert sheet.is_visible() and number.is_visible()
    assert sheet.get_attribute("aria-labelledby") and page.locator("#list-search-more-title").inner_text() == "More filters"
    page.keyboard.press("Escape")
    assert not sheet.is_visible()

    trigger.click()
    number.fill("1234")
    sheet.get_by_role("button", name="Show results").click()
    page.wait_for_url("**number=1234**")
    # Back on the page with a filter set: the sheet still starts closed, the filter is kept.
    assert not page.locator("dialog.bottom-sheet").is_visible()
    trigger.click()
    assert page.locator("dialog.bottom-sheet input[name='number']").input_value() == "1234"

    # Turning the phone into a wide screen puts the filters back in the page.
    page.set_viewport_size({"width": 1280, "height": 800})
    page.wait_for_function("document.querySelector('dialog.bottom-sheet') === null")
    assert page.locator("details.list-search-more > summary").is_visible()


def test_a_responsive_table_is_cards_on_a_phone(client, db_session, tenant_factory, browser_page):
    from datetime import date
    from decimal import Decimal

    from openclear.services import issued_item_service

    tenant, account, users = tenant_factory.make(slug="cards")
    for number in ("501", "502"):
        issued_item_service.create_issued_item(db_session, tenant.id, issued_item_service.IssuedItemInput(
            account_id=account.id, check_number=number, amount=Decimal("12.50"), payee_name="Northgate", issue_date=date(2026, 6, 1)),
            submitted_by_user_id=users["admin"].id)
    db_session.commit()
    _sign_in(client, tenant, users)

    page = browser_page(1280)
    page.goto("http://testserver/ui/issued-items")
    assert page.evaluate("getComputedStyle(document.querySelector('table.responsive tbody tr')).display") == "table-row"
    assert page.locator("table.responsive thead th").count() == 5

    page = browser_page(360)
    page.goto("http://testserver/ui/issued-items")
    card = page.locator("table.responsive tbody tr").first
    assert page.evaluate("getComputedStyle(document.querySelector('table.responsive tbody tr')).display") == "grid"
    title, money = card.locator("td.cell-primary"), card.locator("td.cell-money")
    t, m = title.bounding_box(), money.bounding_box()
    assert t["y"] <= m["y"] + m["height"] / 2 <= t["y"] + t["height"] and m["x"] > t["x"]  # side by side
    assert card.locator("td.cell-primary a").bounding_box()["height"] >= 44
    assert page.evaluate("getComputedStyle(document.querySelector('td.cell-meta'), '::before').content") == '"Payee: "'
    # Sorting still works: the headers are buttons above the cards.
    sort = page.locator("table.responsive thead a.sort-link", has_text="Amount")
    assert sort.is_visible() and sort.bounding_box()["height"] >= 44
    sort.click()
    page.wait_for_url("**sort=amount**")


def test_a_rows_actions_in_a_sheet_on_a_phone(client, tenant_factory, browser_page):
    tenant, _account, users = tenant_factory.make(slug="row-sheet")
    _sign_in(client, tenant, users)

    page = browser_page(1280)
    page.goto("http://testserver/ui/users")
    row = page.locator("table.responsive tbody tr", has_text=users["viewer"].email)
    assert row.get_by_role("button", name="Reset password").is_visible()
    assert not row.locator(".row-menu").is_visible()

    # Users' actions (sheet: "tablet") are in the sheet on a tablet too, the row still a row.
    page = browser_page(768)
    page.goto("http://testserver/ui/users")
    row = page.locator("table.responsive tbody tr", has_text=users["viewer"].email)
    assert page.evaluate("getComputedStyle(document.querySelector('table.responsive tbody tr')).display") == "table-row"
    assert not row.get_by_role("button", name="Reset password").is_visible()
    row.get_by_role("button", name=f"Actions for {users['viewer'].email}").click()
    assert page.get_by_role("dialog", name=f"Actions for {users['viewer'].email}").get_by_role("button", name="Reset password").is_visible()

    page = browser_page(360)
    page.goto("http://testserver/ui/users")
    row = page.locator("table.responsive tbody tr", has_text=users["viewer"].email)
    assert not row.get_by_role("button", name="Reset password").is_visible()
    menu = row.get_by_role("button", name=f"Actions for {users['viewer'].email}")
    box = menu.bounding_box()
    assert box["width"] >= 44 and box["height"] >= 44
    menu.click()
    sheet = page.get_by_role("dialog", name=f"Actions for {users['viewer'].email}")
    assert sheet.is_visible()
    for name in ("Deactivate", "Sign out everywhere", "Reset password"):
        assert sheet.get_by_role("button", name=name).bounding_box()["height"] >= 44
    assert sheet.get_by_role("link", name="Edit").bounding_box()["height"] >= 44
    # Its confirmation opens over the sheet; cancelling leaves the person as they were.
    sheet.get_by_role("button", name="Sign out everywhere").click()
    confirm = page.locator("dialog.confirm-dialog")
    assert confirm.is_visible()
    confirm.get_by_role("button", name="Cancel").click()
    sheet.get_by_role("button", name="Close").click()
    assert not sheet.is_visible()


def test_a_wide_touch_screen_gets_44px_targets(client, tenant_factory, browser_page):
    """A landscape iPad is 1024px or more, so no width query catches it: (pointer: coarse)
    does. The same width with a mouse keeps the desktop's sizes."""
    tenant, _account, users = tenant_factory.make(slug="wide-touch")
    _sign_in(client, tenant, users)

    def heights(page):
        page.goto("http://testserver/ui/users")
        row = page.locator("table.responsive tbody tr", has_text=users["viewer"].email)
        return (row.get_by_role("link", name="Edit").bounding_box()["height"],
                row.get_by_role("button", name="Reset password").bounding_box()["height"],
                page.locator(".help-button").bounding_box()["height"])

    assert all(h >= 44 for h in heights(browser_page(1366, 1024, has_touch=True, is_mobile=True)))
    assert all(h < 44 for h in heights(browser_page(1366, 1024)))


def test_a_setting_opens_full_screen_on_a_phone(client, tenant_factory, browser_page):
    tenant, _account, users = tenant_factory.make(slug="setting-sheet")
    _sign_in(client, tenant, users)

    page = browser_page(360, 780)
    page.goto("http://testserver/ui/settings")
    page.click('a.tile[data-key="timeouts"]')
    page.wait_for_selector("#settings-dialog[open] [data-dialog-body] form")
    box = page.locator("#settings-dialog").bounding_box()
    assert (box["x"], box["y"], box["width"], box["height"]) == (0, 0, 360, 780)
    close = page.get_by_role("button", name="Close")
    assert close.bounding_box()["height"] >= 44 and close.is_visible()
    # The heading and Close stay at the top while the form scrolls under them.
    page.locator("#settings-dialog").evaluate("(d) => { d.scrollTop = d.scrollHeight; }")
    assert 0 <= close.bounding_box()["y"] < 60
    close.click()
    assert not page.locator("#settings-dialog").is_visible()
