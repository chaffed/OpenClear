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
    """Returns open(width) -> a Playwright page at that width, served by the test client."""
    playwright = pytest.importorskip("playwright.sync_api")
    try:
        runner = playwright.sync_playwright().start()
        browser = runner.chromium.launch()
    except Exception as exc:  # noqa: BLE001 -- no browser installed here
        pytest.skip(f"Chromium isn't available: {exc}")

    def serve(route):
        request = route.request
        response = client.request(request.method, request.url.replace("http://testserver", ""),
                                  headers={k: v for k, v in request.headers.items() if k.lower() != "cookie"},
                                  content=request.post_data_buffer, follow_redirects=False)
        route.fulfill(status=response.status_code, headers=dict(response.headers), body=response.content)

    errors: list[str] = []

    def open_at(width: int, height: int = 800):
        context = browser.new_context(viewport={"width": width, "height": height}, reduced_motion="reduce")
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
