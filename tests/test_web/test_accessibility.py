# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""The main pages checked against WCAG 2.1 A and AA with axe-core in a real browser
(UX_PLAN.md U10; MASTER_PLAN.md M2). Pages are served through the test client, so no
server is needed; Chromium comes from Playwright. Skipped where neither is installed (pip
install playwright && playwright install chromium).

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
from tests.conftest import TenantFactory  # noqa: E402

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "axe"
BASELINE = FIXTURES / "baseline.json"
PAGES = [
    ("login", "/ui/login", False),
    ("dashboard", "/ui/", True),
    ("exceptions", "/ui/exceptions", True),
    ("exception", "{exception}", True),
    ("issued_items", "/ui/issued-items", True),
    ("issued_upload", "/ui/issued-items/bulk", True),
    ("search", "/ui/search?q=1001", True),
    ("settings", "/ui/settings", True),
    ("controls", "/ui/admin/controls", True),
    ("action_log", "/ui/audit-log", True),
    ("not_found", "/ui/no-such-page", True),
]


def _problems(results: dict) -> set[str]:
    """One "rule: target" per failing element, stable across runs."""
    found = set()
    for violation in results["violations"]:
        for node in violation["nodes"]:
            found.add(f"{violation['id']} ({violation['impact']}): {' '.join(node['target'])}")
    return found


def test_main_pages_have_no_new_accessibility_problems(client, db_session, tenant_factory):
    try:
        browser_context = playwright.sync_playwright().start()
        browser = browser_context.chromium.launch()
    except Exception as exc:  # noqa: BLE001 -- no browser installed here
        pytest.skip(f"Chromium isn't available: {exc}")

    tenant, account, users = tenant_factory.make(slug="a11y")
    paid = ingest_paid_item(db_session, tenant.id, PaidItemSubmission(account_id=account.id, check_number="1001",
                                                                      presented_amount=Decimal("50.00"), presented_date=date(2026, 3, 2)))
    db_session.commit()
    from openclear.repositories.exception_repo import ExceptionRepository

    exception = ExceptionRepository(db_session, tenant.id).list(source_item_id=paid.id)[0]

    def serve(route):
        request = route.request
        url = request.url.replace("http://testserver", "")
        response = client.request(request.method, url, headers={k: v for k, v in request.headers.items() if k.lower() != "cookie"},
                                  content=request.post_data_buffer, follow_redirects=False)
        route.fulfill(status=response.status_code, headers=dict(response.headers), body=response.content)

    axe = (FIXTURES / "axe.min.js").read_text()
    found: dict[str, list[str]] = {}
    try:
        context = browser.new_context(bypass_csp=True)
        context.route("http://testserver/**", serve)
        page = context.new_page()
        for name, path, signed_in in PAGES:
            if signed_in and not client.cookies.get("access_token"):
                client.get("/ui/login")
                csrf = client.cookies.get("csrf_token")
                client.post("/ui/login", data={"tenant_slug": tenant.slug, "email": users["admin"].email,
                                               "password": TenantFactory.PASSWORD, "csrf_token": csrf, "next": "/ui/"})
            page.goto("http://testserver" + path.format(exception=f"/ui/exceptions/{exception.id}"))
            if signed_in:  # really the page, not a redirect to sign in
                assert "/ui/login" not in page.url, f"{name} redirected to {page.url}"
            page.add_script_tag(content=axe)
            results = page.evaluate("axe.run(document, {runOnly: {type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']}})")
            found[name] = sorted(_problems(results))
    finally:
        browser.close()
        browser_context.stop()

    if os.environ.get("OPENCLEAR_AXE_WRITE_BASELINE"):
        BASELINE.write_text(json.dumps(found, indent=2, sort_keys=True) + "\n")
        return
    baseline = json.loads(BASELINE.read_text()) if BASELINE.exists() else {}
    new = {name: sorted(set(problems) - set(baseline.get(name, []))) for name, problems in found.items()}
    new = {name: problems for name, problems in new.items() if problems}
    assert not new, "New accessibility problems (fix them, see tests/fixtures/axe/README.md):\n" + json.dumps(new, indent=2)
