# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""The main pages checked against WCAG 2.1 A and AA with axe-core in a real browser
(UX_PLAN.md U10; MASTER_PLAN.md M2). Pages are served through the test client, so no
server is needed; Chromium comes from Playwright. Skipped where neither is installed (pip
install playwright && playwright install chromium).

Each page is checked as people see it (UI_PLAN.md UI-D7): light and dark, desktop and phone,
as the bank and as a customer's user, with the default accent and with a bank's own colours (a
dark one, and one only just dark enough for white button text). Contrast in one theme can't
regress unseen in the other.

The test fails on any problem not in tests/fixtures/axe/baseline.json, the audit taken when
this check was added. Set OPENCLEAR_AXE_WRITE_BASELINE=1 to rewrite it (only when fixing
problems, so the list shrinks)."""

import json
import os
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

playwright = pytest.importorskip("playwright.sync_api")

from openclear.networks.check.ingestion import PaidItemSubmission, ingest_paid_item  # noqa: E402
from openclear.services import security_group_service, user_service  # noqa: E402
from openclear.web.security import THEME_COOKIE_NAME  # noqa: E402
from tests.conftest import TenantFactory  # noqa: E402
from tests.items import _make_customer_with_account  # noqa: E402

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "axe"
BASELINE = FIXTURES / "baseline.json"
PAGES = [
    ("login", "/ui/login", False),
    ("platform_login", "/ui/platform/login", False),
    ("dashboard", "/ui/", True),
    ("exceptions", "/ui/exceptions", True),
    ("exception", "{exception}", True),
    ("issued_items", "/ui/issued-items", True),
    ("issued_upload", "/ui/issued-items/bulk", True),
    ("search", "/ui/search?q=1001", True),
    ("settings", "/ui/settings", True),
    ("settings_reminders", "/ui/settings/reminders", True),
    ("settings_security_events", "/ui/settings/security-events", True),
    ("check_return_reasons", "/ui/check-return-reasons", True),
    ("layouts", "/ui/layouts", True),
    ("customer_hub", "/ui/customers", True),
    ("support_access", "/ui/settings/support-access", True),
    ("layout_builder", "/ui/layouts/new?template=fixed_width", True),
    ("controls", "/ui/admin/controls", True),
    ("action_log", "/ui/audit-log", True),
    ("not_found", "/ui/no-such-page", True),
]

# What a customer's user sees (their pages, not the bank's settings).
CUSTOMER_PAGES = [
    ("dashboard", "/ui/?home=1", True),
    ("exceptions", "/ui/exceptions", True),
    ("issued_items", "/ui/issued-items", True),
    ("issued_upload", "/ui/issued-items/bulk", True),
    ("stop_payments", "/ui/stop-payments", True),
    ("search", "/ui/search?q=1001", True),
    ("settings", "/ui/settings", True),
    ("people", "/ui/people", True),
    ("not_found", "/ui/no-such-page", True),
]

DARK_ACCENT = "#0f5b8a"        # dark: its own shade is unreadable on the dark theme
BORDERLINE_ACCENT = "#757575"  # 4.61:1 on white, under 4.5:1 on the light grey of the selected item

# (name, who, theme, viewport width, accent). The first is the original check; its problems
# keep their old baseline keys, the others' are prefixed with the view's name.
VIEWS = [
    ("desktop", "bank", "light", 1280, None),
    ("dark", "bank", "dark", 1280, DARK_ACCENT),
    ("phone", "bank", "light", 390, BORDERLINE_ACCENT),
    ("customer", "customer", "light", 1280, BORDERLINE_ACCENT),
    ("customer-dark-phone", "customer", "dark", 390, DARK_ACCENT),
]


def _problems(results: dict) -> set[str]:
    """One "rule: target" per failing element, stable across runs."""
    found = set()
    for violation in results["violations"]:
        for node in violation["nodes"]:
            found.add(f"{violation['id']} ({violation['impact']}): {' '.join(node['target'])}")
    return found


@pytest.mark.parametrize("view, who, theme, width, accent", VIEWS, ids=[v[0] for v in VIEWS])
def test_main_pages_have_no_new_accessibility_problems(client, db_session, tenant_factory, view, who, theme, width, accent):
    try:
        browser_context = playwright.sync_playwright().start()
        browser = browser_context.chromium.launch()
    except Exception as exc:  # noqa: BLE001 -- no browser installed here
        pytest.skip(f"Chromium isn't available: {exc}")

    tenant, account, users = tenant_factory.make(slug="a11y")
    tenant.accent_color = accent
    email, pages = users["admin"].email, PAGES
    if who == "customer":
        customer, account = _make_customer_with_account(db_session, tenant, "A11Y-1")
        admin_group = security_group_service.get_security_group_by_name(db_session, tenant.id, "Admin")
        email = user_service.create_user_with_membership(db_session, tenant.id, email="ap@a11y-customer.example.com",
                                                         password=TenantFactory.PASSWORD, security_group_id=admin_group.id,
                                                         customer_id=customer.id).email
        pages = CUSTOMER_PAGES
    paid = ingest_paid_item(db_session, tenant.id, PaidItemSubmission(account_id=account.id, check_number="1001",
                                                                      presented_amount=Decimal("50.00"), presented_date=date(2026, 3, 2)))
    db_session.commit()
    from openclear.repositories.exception_repo import ExceptionRepository

    exception = ExceptionRepository(db_session, tenant.id).list(source_item_id=paid.id)[0]
    if theme == "dark":
        client.cookies.set(THEME_COOKIE_NAME, "dark")

    def serve(route):
        request = route.request
        url = request.url.replace("http://testserver", "")
        response = client.request(request.method, url, headers={k: v for k, v in request.headers.items() if k.lower() != "cookie"},
                                  content=request.post_data_buffer, follow_redirects=False)
        route.fulfill(status=response.status_code, headers=dict(response.headers), body=response.content)

    axe = (FIXTURES / "axe.min.js").read_text()
    found: dict[str, list[str]] = {}
    try:
        context = browser.new_context(bypass_csp=True, viewport={"width": width, "height": 900}, color_scheme=theme)
        context.route("http://testserver/**", serve)
        page = context.new_page()
        for name, path, signed_in in pages:
            if signed_in and not client.cookies.get("access_token"):
                client.get("/ui/login")
                csrf = client.cookies.get("csrf_token")
                client.post("/ui/login", data={"tenant_slug": tenant.slug, "email": email,
                                               "password": TenantFactory.PASSWORD, "csrf_token": csrf, "next": "/ui/"})
            page.goto("http://testserver" + path.format(exception=f"/ui/exceptions/{exception.id}"))
            if signed_in:  # really the page, not a redirect to sign in
                assert "/ui/login" not in page.url, f"{name} redirected to {page.url}"
            page.add_script_tag(content=axe)
            results = page.evaluate("axe.run(document, {runOnly: {type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']}})")
            found[name if view == "desktop" else f"{view}/{name}"] = sorted(_problems(results))
    finally:
        browser.close()
        browser_context.stop()

    baseline = json.loads(BASELINE.read_text()) if BASELINE.exists() else {}
    if os.environ.get("OPENCLEAR_AXE_WRITE_BASELINE"):
        mine = {key for key in baseline if (view == "desktop") == ("/" not in key) and (view == "desktop" or key.startswith(view + "/"))}
        BASELINE.write_text(json.dumps({**{k: v for k, v in baseline.items() if k not in mine}, **found}, indent=2, sort_keys=True) + "\n")
        return
    new = {name: sorted(set(problems) - set(baseline.get(name, []))) for name, problems in found.items()}
    new = {name: problems for name, problems in new.items() if problems}
    assert not new, "New accessibility problems (fix them, see tests/fixtures/axe/README.md):\n" + json.dumps(new, indent=2)
