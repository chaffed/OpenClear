#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Pre-renders the 6 Mermaid ER diagrams on the admin Data Dictionary page
(templates/docs/admin/data-dictionary.html) to static PNG files under
static/generated/schema-diagrams/, for the Data Dictionary PDF export
(services/doc_pdf_service.py) to embed as plain <img> tags — WeasyPrint doesn't execute
JavaScript, so the live Mermaid+pan/zoom used by the interactive page can't render there.

PNG rather than SVG: Mermaid's entity/column labels are HTML rendered via
<foreignObject>, which WeasyPrint's own (non-browser) SVG engine doesn't support --
extracted SVG markup renders as blank, textless boxes in the PDF (confirmed by actually
opening a generated PDF and looking, not just checking that generation didn't raise). A
real browser screenshot sidesteps that entirely.

Seeds a throwaway demo tenant in a scratch SQLite database (never the real
.openclear-run/ or project-root openclear.db), runs the actual app against it, and drives a
real headless browser (Playwright) to the data-dictionary page with pan/zoom disabled via
an init script (see static/js/mermaid-diagrams.js's `window.__openclearSkipPanZoom` check),
so every diagram renders at its full natural size instead of clamped to the small on-page
preview box, then screenshots each one by its `data-diagram-slug` attribute (see
_macros/mermaid_diagram.html).

The PNGs are committed to the repo, so this is a manual, occasional dev task, not
something CI runs — rerun it whenever data-dictionary.html's diagram content changes,
then commit the result yourself.

Usage:
    pip install -e ".[dev]"
    playwright install chromium
    python scripts/render_schema_diagrams.py
"""

import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = PROJECT_ROOT / "src" / "openclear" / "static" / "generated" / "schema-diagrams"
HOST = "127.0.0.1"
PORT = 8798
BASE_URL = f"http://{HOST}:{PORT}"

TENANT_NAME = "Schema Diagram Render Bank"
TENANT_SLUG = "schema-diagram-render-bank"
ADMIN_EMAIL = "admin@schemadiagramrender.example.com"
ADMIN_PASSWORD = "DemoPassword123!"
VIEWPORT = {"width": 2800, "height": 1400}  # generous enough to fit the largest diagram at full natural size unclamped

EXPECTED_SLUGS = ["tenancy-access", "positive-pay", "ach", "exceptions-ml", "bulk-export", "platform-audit"]


def _configure_env(run_dir: Path) -> None:
    os.environ["OPENCLEAR_ENVIRONMENT"] = "development"
    os.environ["OPENCLEAR_DATABASE_URL"] = f"sqlite:///{run_dir / 'schema_render.db'}"
    os.environ["OPENCLEAR_CHECK_IMAGE_STORAGE_DIR"] = str(run_dir / "check_images")
    os.environ["OPENCLEAR_TENANT_ASSET_STORAGE_DIR"] = str(run_dir / "tenant_assets")
    os.environ["OPENCLEAR_BULK_UPLOAD_STORAGE_DIR"] = str(run_dir / "bulk_uploads")
    os.environ["OPENCLEAR_ML_ARTIFACT_DIR"] = str(run_dir / "ml_artifacts")


def _seed() -> None:
    import openclear.domain  # noqa: F401
    from openclear.db.base import Base
    from openclear.db.session import get_engine, get_session_factory
    from openclear.domain.payment_network import PaymentNetwork, SettlementTiming
    from openclear.services import provisioning_service

    engine = get_engine()
    Base.metadata.create_all(engine)

    session = get_session_factory()()
    try:
        session.add_all(
            [
                PaymentNetwork(code="check", name="Check", settlement_timing=SettlementTiming.ASYNC_REVIEWABLE),
                PaymentNetwork(code="ach", name="ACH", settlement_timing=SettlementTiming.ASYNC_REVIEWABLE),
            ]
        )
        session.commit()
        provisioning_service.create_tenant_with_admin(
            session, tenant_name=TENANT_NAME, tenant_slug=TENANT_SLUG, admin_email=ADMIN_EMAIL, admin_password=ADMIN_PASSWORD
        )
        session.commit()
    finally:
        session.close()


def _wait_for_server(deadline_seconds: float = 30) -> None:
    deadline = time.monotonic() + deadline_seconds
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((HOST, PORT), timeout=0.5):
                return
        except OSError:
            time.sleep(0.2)
    raise RuntimeError("server didn't come up")


def _render() -> None:
    from playwright.sync_api import sync_playwright

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context(viewport=VIEWPORT)
        # Read by mermaid-diagrams.js's DOMContentLoaded handler on every subsequent
        # navigation in this context -- runs before any page script, so it's in place
        # before the app's own script executes.
        context.add_init_script("window.__openclearSkipPanZoom = true;")
        page = context.new_page()

        page.goto(f"{BASE_URL}/ui/login")
        page.wait_for_selector("form")
        page.fill('input[name="tenant_slug"]', TENANT_SLUG)
        page.fill('input[name="email"]', ADMIN_EMAIL)
        page.fill('input[name="password"]', ADMIN_PASSWORD)
        page.click('button[type="submit"]')
        page.wait_for_load_state("networkidle")

        page.goto(f"{BASE_URL}/ui/docs/admin/data-dictionary")
        page.wait_for_load_state("networkidle")
        # Scoped to .mermaid-container specifically -- a bare ".mermaid-wrap svg" selector
        # also matches the toolbar's own icon <svg> elements, which this script hides (see
        # mermaid-diagrams.js's __openclearSkipPanZoom branch) and so would never satisfy a
        # "become visible" wait if Playwright happened to resolve to one of those first.
        page.wait_for_selector(".mermaid-container svg", timeout=15000)
        page.wait_for_timeout(500)

        found_slugs = []
        for wrap in page.locator(".mermaid-wrap").all():
            slug = wrap.get_attribute("data-diagram-slug")
            out_path = OUTPUT_DIR / f"{slug}.png"
            wrap.locator(".mermaid-container svg").screenshot(path=str(out_path))
            found_slugs.append(slug)
            print(f"  wrote {out_path.relative_to(PROJECT_ROOT)} ({out_path.stat().st_size} bytes)")

        context.close()
        browser.close()

    missing = set(EXPECTED_SLUGS) - set(found_slugs)
    if missing:
        raise RuntimeError(f"missing expected diagram slugs: {sorted(missing)}")
    print(f"Done — {len(found_slugs)} diagrams written to {OUTPUT_DIR}")


def main() -> None:
    os.chdir(PROJECT_ROOT)
    run_dir = Path(tempfile.mkdtemp(prefix="openclear-schema-render-"))
    _configure_env(run_dir)
    server = None
    try:
        _seed()
        server = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "openclear.main:app", "--host", HOST, "--port", str(PORT), "--log-level", "warning"],
            cwd=PROJECT_ROOT, env=os.environ.copy(),
        )
        _wait_for_server()
        _render()
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
