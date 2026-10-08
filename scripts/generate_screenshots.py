#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Regenerates the screenshots referenced by README.md.

Seeds a throwaway demo tenant with realistic-looking data in a scratch SQLite database
(never the real .openclear-run/ or project-root openclear.db), runs the actual app against it,
and drives a real headless browser (Playwright) through the key screens to capture PNGs
into docs/screenshots/ — once in light mode (the existing filenames, e.g. dashboard.png)
and once in dark mode (a `_dark` suffix, e.g. dashboard_dark.png), forcing the theme via
the same cookie web/routers/theme.py sets rather than relying on OS color-scheme alone.
The screenshots are committed to the repo, so this is a manual, occasional dev task, not
something CI runs, and it does not commit or push anything itself — rerun it after a UI
change significant enough to make the committed images visibly stale, then commit the
result yourself.

Usage:
    pip install -e ".[dev]"
    playwright install chromium
    python scripts/generate_screenshots.py
"""

import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCREENSHOT_DIR = PROJECT_ROOT / "docs" / "screenshots"
HOST = "127.0.0.1"
PORT = 8799
BASE_URL = f"http://{HOST}:{PORT}"

TENANT_NAME = "Riverside Community Bank"
TENANT_SLUG = "riverside-bank"
ADMIN_EMAIL = "admin@riversidebank.example.com"
ADMIN_PASSWORD = "DemoPassword123!"
VIEWPORT = {"width": 1440, "height": 900}


def _configure_env(run_dir: Path) -> None:
    # Mirrors scripts/launcher.py::_configure_run_env's pattern — must run before any
    # `from openclear...` import, since config.Settings is @lru_cache'd. Points everything
    # at a throwaway temp dir so this never touches the real .openclear-run/ or a
    # project-root openclear.db.
    os.environ["OPENCLEAR_ENVIRONMENT"] = "development"
    os.environ["OPENCLEAR_DATABASE_URL"] = f"sqlite:///{run_dir / 'screenshots.db'}"
    os.environ["OPENCLEAR_CHECK_IMAGE_STORAGE_DIR"] = str(run_dir / "check_images")
    os.environ["OPENCLEAR_TENANT_ASSET_STORAGE_DIR"] = str(run_dir / "tenant_assets")
    os.environ["OPENCLEAR_BULK_UPLOAD_STORAGE_DIR"] = str(run_dir / "bulk_uploads")
    os.environ["OPENCLEAR_ML_ARTIFACT_DIR"] = str(run_dir / "ml_artifacts")


def _seed_demo_data() -> None:
    # Registers each network's adapter (register_adapter side effect) — normally done by
    # main.py::create_app(), but seeding here runs in this process, before the app
    # subprocess even starts.
    import openclear.networks.ach  # noqa: F401
    import openclear.networks.check  # noqa: F401
    import openclear.domain  # noqa: F401  (populates Base.metadata with every model)

    from openclear.db.base import Base
    from openclear.db.session import get_engine, get_session_factory
    from openclear.services import demo_tenant_service, provisioning_service

    engine = get_engine()
    Base.metadata.create_all(engine)

    session = get_session_factory()()
    try:
        # This script's own scratch database has no baseline migration data, unlike a
        # real app deployment (see 9327972d0952_seed_payment_network_rows_for_check_and_
        # .py) — Base.metadata.create_all above only creates the tables, not their rows.
        from openclear.domain.payment_network import PaymentNetwork, SettlementTiming

        session.add_all(
            [
                PaymentNetwork(code="check", name="Check", settlement_timing=SettlementTiming.ASYNC_REVIEWABLE),
                PaymentNetwork(code="ach", name="ACH", settlement_timing=SettlementTiming.ASYNC_REVIEWABLE),
            ]
        )
        session.commit()

        print(f"Seeding tenant {TENANT_NAME!r}...")
        identity = provisioning_service.create_tenant_with_admin(
            session, tenant_name=TENANT_NAME, tenant_slug=TENANT_SLUG, admin_email=ADMIN_EMAIL, admin_password=ADMIN_PASSWORD
        )
        session.commit()

        # The rest of the dataset (accounts, customer, issued/paid items, stop payment,
        # ACH, a trained per-customer ML model, branding) is the exact same seed
        # services/demo_tenant_service.py uses for the persistent live demo tenant — one
        # definition, not two that can drift apart. This script's own tenant here is a
        # throwaway in a scratch DB, so it's fine that is_demo is never set on it.
        demo_tenant_service.seed_demo_content(session, identity.tenant, identity.admin_user, password=ADMIN_PASSWORD)
        _seed_billing(session, identity.tenant.id)

        print("Demo data seeded.")
    finally:
        session.close()


# Example prices for the billing screenshots; the starter schedule itself is priced at zero.
BILLING_PRICES = {
    "PP-MAINT": "35.00", "PP-ACCT": "15.00", "ACHPP-ACCT": "12.00", "PP-USER": "2.00", "PP-ISSUE": "0.03",
    "PP-ISSFILE": "1.50", "PP-ITEM": "0.08", "PP-EXCPT": "1.25", "PP-RETURN": "2.50", "PP-PAYEE": "0.10",
    "STOP-PAY": "15.00", "ACHPP-ITEM": "0.06", "ACHPP-EXCPT": "1.25", "ACHPP-RETURN": "2.50", "ACHPP-RULE": "1.00",
}


def _seed_billing(session, tenant_id) -> None:
    """A priced fee schedule, with the seeded customer on hard charge at a 10% discount
    (the bank's default stays account analysis), for the Billing screenshots."""
    from sqlalchemy import select

    from openclear.domain.billing import BillingMethod
    from openclear.domain.customer import Customer
    from openclear.services.billing import terms as billing_terms

    for fee in billing_terms.add_starter_schedule(session, tenant_id):
        billing_terms.update_fee_item(session, tenant_id, fee.id, unit_price=BILLING_PRICES.get(fee.code, "0"))
    customer = session.execute(select(Customer).where(Customer.tenant_id == tenant_id)).scalars().first()
    if customer is not None:
        billing_terms.set_customer_terms(session, tenant_id, customer.id, billing_method=BillingMethod.HARD_CHARGE, discount_percent="10")
    session.commit()


def _wait_for_server(deadline_seconds: float = 30) -> None:
    deadline = time.monotonic() + deadline_seconds
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((HOST, PORT), timeout=0.5):
                return
        except OSError:
            time.sleep(0.2)
    raise RuntimeError(f"Server did not come up on {HOST}:{PORT} within {deadline_seconds}s")


@dataclass(frozen=True, slots=True)
class Page:
    name: str
    path: str
    wait_for_selector: str | None = None


PAGES: list[Page] = [
    Page("dashboard", "/ui/", "table, .card"),
    Page("issued_items", "/ui/issued-items"),
    Page("paid_items", "/ui/paid-items"),
    Page("paid_items_bulk", "/ui/paid-items/bulk"),
    Page("exceptions", "/ui/exceptions"),
    Page("check_images_bulk", "/ui/check-images/bulk"),
    Page("ach_transactions", "/ui/ach/transactions"),
    Page("stop_payments", "/ui/stop-payments"),
    Page("accounts", "/ui/accounts"),
    Page("customers", "/ui/customers"),
    Page("users", "/ui/users"),
    Page("security_groups", "/ui/security-groups"),
    Page("settings", "/ui/settings"),
    Page("admin_ml_models", "/ui/admin"),
    Page("audit_log", "/ui/audit-log"),
    Page("billing_fees", "/ui/billing/fees"),
]

# The seeded activity is dated today, so bill the current month.
BILLING_PERIOD = time.strftime("%Y-%m")


def _run_capture_pass(browser, theme: str) -> None:
    from openclear.web.security import THEME_COOKIE_NAME

    # suffix distinguishes the dark-mode set on disk without disturbing the existing
    # light-mode filenames README.md already references (dashboard.png stays
    # dashboard.png; its dark twin is dashboard_dark.png).
    suffix = "" if theme == "light" else f"_{theme}"

    context = browser.new_context(viewport=VIEWPORT, color_scheme=theme)
    if theme == "dark":
        # Forces the cookie-based override (see web/routers/theme.py) rather than relying
        # on color_scheme alone -- the app's dark mode is an explicit Light/Dark/System
        # user preference, not just a `prefers-color-scheme` media query, so the
        # System-scoped color_scheme setting above wouldn't otherwise be enough on its own.
        context.add_cookies([{"name": THEME_COOKIE_NAME, "value": "dark", "url": BASE_URL}])
    page = context.new_page()

    print(f"Capturing login page ({theme})...")
    page.goto(f"{BASE_URL}/ui/login")
    page.wait_for_selector("form")
    page.screenshot(path=str(SCREENSHOT_DIR / f"login{suffix}.png"))

    page.fill('input[name="tenant_slug"]', TENANT_SLUG)
    page.fill('input[name="email"]', ADMIN_EMAIL)
    page.fill('input[name="password"]', ADMIN_PASSWORD)
    page.click('button[type="submit"]')
    page.wait_for_load_state("networkidle")

    for spec in PAGES:
        print(f"Capturing {spec.name} ({theme})...")
        page.goto(f"{BASE_URL}{spec.path}")
        if spec.wait_for_selector:
            page.wait_for_selector(spec.wait_for_selector, timeout=10_000)
        page.wait_for_load_state("networkidle")
        page.screenshot(path=str(SCREENSHOT_DIR / f"{spec.name}{suffix}.png"), full_page=True)

    # A single exception's detail/review page, not just the queue — needs a real id.
    # Picks a still-*open* row (not an already-decided one) so the screenshot shows
    # the actual pay/return review form, not just a read-only past decision.
    print(f"Capturing exception_detail ({theme})...")
    page.goto(f"{BASE_URL}/ui/exceptions")
    page.wait_for_load_state("networkidle")
    open_row_link = page.query_selector(
        'table tr:has(td .badge:text-is("open")) a[href^="/ui/exceptions/"]'
    ) or page.query_selector('table a[href^="/ui/exceptions/"]')
    if open_row_link is not None:
        open_row_link.click()
        page.wait_for_load_state("networkidle")
        page.screenshot(path=str(SCREENSHOT_DIR / f"exception_detail{suffix}.png"), full_page=True)
    else:
        print("  No exceptions found to open — skipping exception_detail screenshot")

    # Billing: the month's overview, then the seeded customer's statement from it.
    print(f"Capturing billing ({theme})...")
    page.goto(f"{BASE_URL}/ui/billing?period={BILLING_PERIOD}")
    page.wait_for_load_state("networkidle")
    page.screenshot(path=str(SCREENSHOT_DIR / f"billing{suffix}.png"), full_page=True)
    statement_link = page.query_selector('table a[href*="/ui/billing/statement"]:not([href*="customer=bank"])')
    if statement_link is not None:
        statement_link.click()
        page.wait_for_load_state("networkidle")
        page.screenshot(path=str(SCREENSHOT_DIR / f"billing_statement{suffix}.png"), full_page=True)
    else:
        print("  No customer statement found — skipping billing_statement screenshot")

    context.close()


def _capture_screenshots() -> None:
    from playwright.sync_api import sync_playwright

    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch()
        _run_capture_pass(browser, "light")
        _run_capture_pass(browser, "dark")
        browser.close()


def main() -> None:
    os.chdir(PROJECT_ROOT)  # pin cwd regardless of how this was invoked — dev_keys/ and
    # every other *_key_path config default is a relative path resolved against cwd, same
    # gotcha scripts/launcher.py::main() already documents for itself.

    # services/demo_tenant_service.py logs its own seeding progress via logging, not
    # print() -- surface it on the console the same way this script's own print()
    # messages already are, since demo_tenant_service.seed_demo_content() now does most
    # of the actual seeding work below.
    import logging

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    try:
        import playwright  # noqa: F401
    except ImportError:
        print("Playwright isn't installed. Run:\n  pip install -e \".[dev]\"\n  playwright install chromium", file=sys.stderr)
        sys.exit(1)

    run_dir = Path(tempfile.mkdtemp(prefix="openclear-screenshots-"))
    _configure_env(run_dir)

    server: subprocess.Popen | None = None
    try:
        _seed_demo_data()

        print(f"Starting the app on {BASE_URL} ...")
        server = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "openclear.main:app", "--host", HOST, "--port", str(PORT), "--log-level", "warning"],
            cwd=PROJECT_ROOT,
            env=os.environ.copy(),
        )
        _wait_for_server()

        _capture_screenshots()
        print(f"\nDone — screenshots written to {SCREENSHOT_DIR}")
    finally:
        if server is not None:
            server.terminate()
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()
        shutil.rmtree(run_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
