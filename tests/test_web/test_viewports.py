# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""The key pages measured on a phone, a tablet and two desktop widths (MOBILE_PLAN.md R0).
At each width: no sideways page scroll, touch targets of at least 44x44px (below 1024px),
Pay and Return visible without scrolling on an exception, and axe clean (WCAG 2.1 A, AA).
At 1280px each page's screenshot is compared with the reference, so phone and tablet work
can't change the desktop.

Pages are served through the test client, as in test_accessibility.py; skipped where
Playwright's Chromium isn't installed. The test fails on any problem not in
tests/fixtures/viewport/baseline.json, and on a 1280px screenshot that differs from
tests/fixtures/viewport/<platform>/ (compared only on the platform the references were
taken on, since fonts differ elsewhere, and on SQLite, since lists sorted by when rows were
made break ties in another order on Postgres). Set OPENCLEAR_VIEWPORT_WRITE_BASELINE=1 to rewrite the
baseline, the screenshots and REPORT.md (only when fixing problems, or after a deliberate
desktop change). See tests/fixtures/viewport/README.md."""

import io
import json
import os
import re
import shutil
import sys
from pathlib import Path

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, text

playwright = pytest.importorskip("playwright.sync_api")

from openclear.domain.ach_transaction import AchTransaction  # noqa: E402
from openclear.domain.customer import Customer  # noqa: E402
from openclear.domain.exception_item import ExceptionItem  # noqa: E402
from openclear.domain.issued_item import IssuedItem  # noqa: E402
from openclear.domain.paid_item import PaidItem  # noqa: E402
from openclear.domain.tenant_membership import TenantMembership  # noqa: E402
from openclear.domain.user import User  # noqa: E402
from openclear.repositories.exception_repo import ExceptionRepository  # noqa: E402
from openclear.services import api_client_service, customer_people_service, demo_tenant_service, security_group_service, user_service  # noqa: E402
from openclear.web.rate_limit import limiter  # noqa: E402
from tests.conftest import TenantFactory  # noqa: E402

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
HERE = FIXTURES / "viewport"
BASELINE = HERE / "baseline.json"
REPORT = HERE / "REPORT.md"
SCREENSHOTS = HERE / sys.platform  # compared on SQLite only: Postgres breaks ties between rows differently
DIFFS = Path(__file__).resolve().parents[2] / ".openclear-run" / "viewport-diffs"
SHOTS = Path(__file__).resolve().parents[2] / ".openclear-run" / "viewport-shots"  # OPENCLEAR_VIEWPORT_SHOTS=1: every width, to look at

VIEWPORTS = {360: 780, 768: 1024, 1024: 768, 1280: 800}  # width: height
REFERENCE_WIDTH = 1280
TOLERANCE = 24  # per colour channel: anti-aliasing varies a little between runs
TOUCH_TARGET = 44
# (name, path, who): the bank's administrator, or the customer's (People is theirs).
PAGES = [
    ("dashboard", "/ui/", "bank"),
    ("exceptions", "/ui/exceptions", "bank"),
    ("exception", "{exception}", "bank"),
    ("issued_items", "/ui/issued-items", "bank"),
    ("paid_items", "/ui/paid-items", "bank"),
    ("stop_payments", "/ui/stop-payments", "bank"),
    ("ach_transactions", "/ui/ach/transactions", "bank"),
    ("ach_authorizations", "/ui/ach/authorizations", "bank"),
    ("accounts", "/ui/accounts", "bank"),
    ("customers", "/ui/customers", "bank"),
    ("api_clients", "/ui/admin/api-clients", "bank"),
    ("action_log", "/ui/audit-log", "bank"),
    ("users", "/ui/users", "bank"),
    ("sign_on", "/ui/admin/sso", "bank"),
    ("control_panel", "/ui/settings", "bank"),
    ("upload_form", "/ui/issued-items/bulk", "bank"),
    ("upload", "{upload}", "bank"),
    # R3: the rest of the bank's pages, and a setting opened in its window from the control panel.
    ("approvals", "/ui/exceptions/approvals", "bank"),
    ("customer", "{customer}", "bank"),
    ("user_edit", "{member}/edit", "bank"),
    ("user_new", "/ui/users/new", "bank"),
    ("users_bulk", "/ui/users/bulk", "bank"),
    ("security_groups", "/ui/security-groups", "bank"),
    ("security_group_edit", "{group}/edit", "bank"),
    ("check_return_reasons", "/ui/check-return-reasons", "bank"),
    ("ach_return_reasons", "/ui/ach-return-reasons", "bank"),
    ("check_images", "/ui/check-images", "bank"),
    ("webhooks", "/ui/admin/webhooks", "bank"),
    ("webhook_new", "/ui/admin/webhooks/new", "bank"),
    ("api_client_new", "/ui/admin/api-clients/new", "bank"),
    ("sso_new", "/ui/admin/sso/new", "bank"),
    ("controls", "/ui/admin/controls", "bank"),
    ("changes", "/ui/admin/changes", "bank"),
    ("model_risk", "/ui/admin/model-risk", "bank"),
    ("ml_models", "/ui/admin/ml-models", "bank"),
    ("plugins", "/ui/admin/plugins", "bank"),
    ("integration", "/ui/admin/integration", "bank"),
    ("layouts", "/ui/layouts", "bank"),
    ("billing", "/ui/billing", "bank"),
    ("wizard", "/ui/wizard/bank", "bank"),
    ("search", "/ui/search?q=Summit", "bank"),
    ("my_security", "/ui/security", "bank"),
    ("notifications", "/ui/security/notifications", "bank"),
    ("setting_branding", "/ui/settings/branding", "bank"),
    ("setting_password_policy", "/ui/settings/password-policy", "bank"),
    ("setting_reminders", "/ui/settings/reminders", "bank"),
    ("setting_security_events", "/ui/settings/security-events", "bank"),
    ("setting_sod", "/ui/settings/segregation-of-duties", "bank"),
    ("setting_support_access", "/ui/settings/support-access", "bank"),
    ("setting_fraud_model", "/ui/settings/fraud-model", "bank"),
    ("settings_dialog", "/ui/settings#timeouts", "bank"),
    ("people", "/ui/people", "customer"),
    ("person", "{person}", "customer"),
    ("people_invite", "/ui/people/invite", "customer"),
    ("people_activity", "/ui/people/activity", "customer"),
    ("customer_control_panel", "/ui/settings", "customer"),
]
# Pages measured after a step: (selector clicked, selector waited for).
STEPS = {"settings_dialog": ('a.tile[data-key="timeouts"]', "#settings-dialog[open] [data-dialog-body] form")}

# Run in the page: every problem at this width as a stable "kind: what" string (digits
# become # so rows of the same kind collapse into one), and the details for the report.
MEASURE = """({width, touch, decision}) => {
  const W = window.innerWidth, H = window.innerHeight;
  const shown = (el) => {
    const r = el.getBoundingClientRect();
    // Off the page (a skip link until it's focused) or inside a closed <details> isn't shown.
    return r.width > 1 && r.height > 1 && r.bottom > 0 && r.right > 0 && el.checkVisibility({visibilityProperty: true});
  };
  // A control's name as a person reads it (never a value, which can be an id).
  const label = (el) => (el.getAttribute('aria-label') || el.innerText || el.querySelector('img[alt]')?.alt
      || (el.labels && el.labels[0] ? el.labels[0].innerText : '')
      || (['submit', 'button'].includes(el.type) ? el.value : '') || el.placeholder || el.name || '')
    .trim().replace(/\\s+/g, ' ').replace(/\\d/g, '#').slice(0, 40);
  const name = (el) => el.tagName.toLowerCase() + (el.tagName === 'INPUT' ? `[${el.type}]` : '')
    + Array.from(el.classList).map((c) => '.' + c).join('') + ` "${label(el)}"`;
  const problems = [], details = [];

  if (document.documentElement.scrollWidth > W + 1) {
    const wide = Array.from(document.querySelectorAll('body *'))
      .filter((e) => shown(e) && e.getBoundingClientRect().right > W + 1 && !e.closest('.table-scroll'))
      .filter((e) => !Array.from(e.children).some((c) => c.getBoundingClientRect().right > W + 1));
    problems.push('scrolls sideways');
    details.push(`scrolls sideways to ${document.documentElement.scrollWidth}px: `
      + Array.from(new Set(wide.slice(0, 8).map(name))).join(', '));
  }

  if (touch) {
    const targets = 'a[href], button, input:not([type=hidden]), select, textarea, summary, [role=button]';
    const small = new Counter();
    document.querySelectorAll(targets).forEach((el) => {
      // A checkbox or radio inside its label is the size of the label.
      const box = (el.type === 'checkbox' || el.type === 'radio') && el.closest('label') ? el.closest('label') : el;
      if (!shown(box)) return;
      // WCAG 2.5.8's exception: a link inside a sentence or block of text.
      if (el.tagName === 'A' && getComputedStyle(el).display === 'inline'
          && el.parentElement.innerText.trim().length > el.innerText.trim().length + 2) return;
      const r = box.getBoundingClientRect();
      if (r.width < touch - 0.5 || r.height < touch - 0.5) small.add(name(el), `${Math.round(r.width)}x${Math.round(r.height)}`);
    });
    small.entries().forEach(([key, sizes]) => {
      problems.push('small target: ' + key);
      details.push(`small target: ${key} (${sizes.length}x, e.g. ${sizes[0]})`);
    });
  }

  if (decision) {
    // Return is its <summary> on a desktop, and a button opening the reasons' sheet below it.
    ['[data-decision-panel] .button-pay',
     '[data-decision-panel] .decision-return > summary, [data-decision-panel] > .sheet-trigger.button-return'].forEach((selector) => {
      const el = Array.from(document.querySelectorAll(selector)).find(shown);
      if (!el) { problems.push('decision button missing: ' + selector); return; }
      const r = el.getBoundingClientRect();
      if (r.top < 0 || r.bottom > H) {
        problems.push('decision button needs scrolling: ' + selector);
        details.push(`decision button needs scrolling: ${selector} (top ${Math.round(r.top)}px, screen ${H}px)`);
      }
    });
  }
  return {problems, details};

  function Counter() {
    const m = new Map();
    return {add: (k, v) => m.set(k, (m.get(k) || []).concat([v])), entries: () => Array.from(m.entries())};
  }
}"""

# Before a reference screenshot: times and dates are whatever "now" was when the data was
# made, so they're replaced by fixed text (the same text every run), and the caret hidden.
STEADY = r"""() => {
  document.querySelectorAll('time').forEach((el) => { el.textContent = 'Jan 1, 2026, 9:00 AM'; });
  const stamp = /\b\d{4}-\d{2}-\d{2}([ T]\d{2}:\d{2}(:\d{2})?)?(\s*UTC)?/g;
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    if (stamp.test(node.nodeValue)) node.nodeValue = node.nodeValue.replace(stamp, '2026-01-01 09:00');
    stamp.lastIndex = 0;
  }
  if (document.activeElement) document.activeElement.blur();
}"""


def _axe_problems(results: dict) -> list[str]:
    return sorted({f"axe {v['id']} ({v['impact']}): {' '.join(n['target'])}" for v in results["violations"] for n in v["nodes"]})


def _sign_in(client, slug: str, email: str) -> str:
    client.cookies.clear()
    client.get("/ui/login")
    csrf = client.cookies.get("csrf_token")
    response = client.post("/ui/login", data={"tenant_slug": slug, "email": email, "password": TenantFactory.PASSWORD,
                                              "csrf_token": csrf, "next": "/ui/"}, follow_redirects=False)
    assert response.status_code == 303, response.text
    return client.cookies.get("csrf_token")


def _seed(client, db_session, tenant_factory):
    """The demo bank's data (services/demo_tenant_service.py: accounts, a customer,
    exceptions open and decided, a stop payment, ACH), the customer's own administrator,
    and one upload with a failed row. Returns the bank's slug, who signs in for each kind
    of page, and the paths of the exception and the upload."""
    tenant, _account, users = tenant_factory.make(slug="viewport", name="Riverside Community Bank")
    demo_tenant_service.seed_demo_content(db_session, tenant, users["admin"], password=TenantFactory.PASSWORD,
                                          email_domain="riverside.example.com", customer_contact_email="dana@dental.example.com",
                                          tenant_name="Riverside Community Bank")
    customer = db_session.execute(select(Customer).where(Customer.tenant_id == tenant.id)).scalar_one()
    customer.self_manages_users = True
    role = security_group_service.create_security_group(db_session, tenant.id, security_group_service.SecurityGroupInput(
        name="Customer admin", permissions=["customer_user:manage", "issued_item:read", "issued_item:write"], offered_to_customers=True))
    groups = {g.name: g for g in security_group_service.list_security_groups(db_session, tenant.id)}
    for number, (name, group, read_only) in enumerate((("Accounting system", "Preparer", False), ("Reporting", "Viewer", True)), start=1):
        issued = api_client_service.create_client(db_session, tenant.id, name=name, read_only=read_only,
                                                  security_group_id=groups[group].id)
        issued.client.client_id = f"oc_viewport_client_{number}"  # made at random; fixed for the pictures
    customer_admin = user_service.create_user_with_membership(db_session, tenant.id, email="dana@dental.example.com",
                                                              password=TenantFactory.PASSWORD, security_group_id=role.id,
                                                              customer_id=customer.id)
    customer_people_service.invite(db_session, tenant.id, customer.id, email="sam@dental.example.com", security_group_id=role.id,
                                   invited_by_user_id=customer_admin.id, accept_base_url="http://testserver")
    # Lists sort by when people were added; made in one go, some tie, so give each its own time.
    rows = db_session.execute(select(TenantMembership, User.email).join(User, User.id == TenantMembership.user_id)
                              .where(TenantMembership.tenant_id == tenant.id).order_by(User.email)).all()
    for minutes, (membership, _email) in enumerate(rows):
        membership.created_at = datetime(2026, 1, 1, 9) + timedelta(minutes=minutes)
    db_session.commit()

    paid = db_session.execute(select(PaidItem).where(PaidItem.tenant_id == tenant.id, PaidItem.check_number == "10025")).scalar_one()
    exception = ExceptionRepository(db_session, tenant.id).list(source_item_id=paid.id)[0]
    assert exception.status.value == "open"

    csrf = _sign_in(client, tenant.slug, users["admin"].email)
    content = (b"account_number,check_number,amount,payee_name,issue_date\n"
               b"1000234561,7001,250.00,Northgate Office Supply,2026-06-15\n"
               b"1000234561,7002,1O.00,Summit Roofing LLC,2026-06-15\n")
    response = client.post("/ui/issued-items/bulk", data={"csrf_token": csrf, "layout_id": ""},
                           files={"upload_file": ("june.csv", content, "text/csv")})
    upload = re.search(r"/ui/bulk-uploads/[0-9a-f-]+", str(response.url)).group(0)

    # Lists sort newest first, then by id: rows made in the same second would come in a
    # different order each run. Give each its own time, in the order they were made (recent,
    # so "oldest item waiting" reads the same every day).
    start = datetime.now(timezone.utc) - timedelta(minutes=30)
    made_in_order = text("rowid") if db_session.get_bind().dialect.name == "sqlite" else None
    for model in (IssuedItem, PaidItem, AchTransaction, ExceptionItem):
        order = [model.created_at] + ([made_in_order] if made_in_order is not None else [model.id])
        for seconds, row in enumerate(db_session.execute(select(model).where(model.tenant_id == tenant.id).order_by(*order)).scalars()):
            row.created_at = start + timedelta(seconds=seconds)
    db_session.commit()
    group = next(g for g in security_group_service.list_security_groups(db_session, tenant.id) if g.name == "Preparer")
    preparer = db_session.execute(select(TenantMembership).join(User, User.id == TenantMembership.user_id)
                                  .where(TenantMembership.tenant_id == tenant.id, User.email == users["preparer"].email)).scalar_one()
    person = db_session.execute(select(TenantMembership).where(TenantMembership.user_id == customer_admin.id)).scalar_one()
    paths = {"exception": f"/ui/exceptions/{exception.id}", "upload": upload, "customer": f"/ui/customers/{customer.id}",
             "member": f"/ui/users/{preparer.id}", "group": f"/ui/security-groups/{group.id}", "person": f"/ui/people/{person.id}"}
    return tenant.slug, {"bank": users["admin"].email, "customer": customer_admin.email}, paths


def _identical(reference: Path, taken: bytes) -> bool:
    from PIL import Image, ImageChops

    old = Image.open(reference).convert("RGB")
    new = Image.open(io.BytesIO(taken)).convert("RGB")
    return old.size == new.size and ImageChops.difference(old, new).point(lambda v: 255 if v > TOLERANCE else 0).getbbox() is None


def _same_picture(reference: Path, taken: bytes, diff_to: Path) -> str | None:
    """None when the screenshot matches the reference, otherwise what differs (and the
    difference saved as a picture to look at)."""
    from PIL import Image, ImageChops

    old = Image.open(reference).convert("RGB")
    new = Image.open(io.BytesIO(taken)).convert("RGB")
    if old.size != new.size:
        diff_to.parent.mkdir(parents=True, exist_ok=True)
        new.save(diff_to.with_suffix(".new.png"))
        return f"size {old.size} became {new.size}"
    box = ImageChops.difference(old, new).point(lambda v: 255 if v > TOLERANCE else 0).getbbox()
    if box is None:
        return None
    diff_to.parent.mkdir(parents=True, exist_ok=True)
    new.save(diff_to.with_suffix(".new.png"))
    ImageChops.difference(old, new).save(diff_to.with_suffix(".diff.png"))
    return f"pixels differ in {box}"


def _write_report(found: dict, details: dict) -> None:
    widths = list(VIEWPORTS)
    lines = ["# Viewport baseline (MOBILE_PLAN.md R0)", "",
             "Written by `tests/test_web/test_viewports.py` with `OPENCLEAR_VIEWPORT_WRITE_BASELINE=1`.",
             "Each cell: sideways scroll (↔), small touch targets (kinds), Pay/Return below the fold (⬇), axe problems.", "",
             "| Page | " + " | ".join(f"{w}px" for w in widths) + " |", "|---|" + "---|" * len(widths)]
    for page_name, _path, _who in PAGES:
        cells = []
        for width in widths:
            problems = found[page_name][str(width)]
            parts = []
            if "scrolls sideways" in problems:
                parts.append("↔")
            small = sum(p.startswith("small target") for p in problems)
            if small:
                parts.append(f"{small} small")
            if any(p.startswith("decision button") for p in problems):
                parts.append("⬇")
            axe = sum(p.startswith("axe") for p in problems)
            if axe:
                parts.append(f"{axe} axe")
            cells.append(", ".join(parts) or "✓")
        lines.append(f"| {page_name} | " + " | ".join(cells) + " |")
    lines += ["", "## Details", ""]
    for page_name, _path, _who in PAGES:
        for width in widths:
            rows = details[page_name][str(width)] + [p for p in found[page_name][str(width)] if p.startswith("axe")]
            if rows:
                lines.append(f"### {page_name} at {width}px")
                lines += [f"- {row}" for row in rows] + [""]
    REPORT.write_text("\n".join(lines).rstrip() + "\n")


def test_key_pages_on_phones_tablets_and_desktops(client, db_session, tenant_factory):
    try:
        runner = playwright.sync_playwright().start()
        browser = runner.chromium.launch()
    except Exception as exc:  # noqa: BLE001 -- no browser installed here
        pytest.skip(f"Chromium isn't available: {exc}")

    slug, people, paths = _seed(client, db_session, tenant_factory)

    def serve(route):
        request = route.request
        response = client.request(request.method, request.url.replace("http://testserver", ""),
                                  headers={k: v for k, v in request.headers.items() if k.lower() != "cookie"},
                                  content=request.post_data_buffer, follow_redirects=False)
        route.fulfill(status=response.status_code, headers=dict(response.headers), body=response.content)

    writing = bool(os.environ.get("OPENCLEAR_VIEWPORT_WRITE_BASELINE"))
    shutil.rmtree(DIFFS, ignore_errors=True)
    axe = (FIXTURES / "axe" / "axe.min.js").read_text()
    found: dict[str, dict[str, list[str]]] = {name: {} for name, _path, _who in PAGES}
    details: dict[str, dict[str, list[str]]] = {name: {} for name, _path, _who in PAGES}
    signed_in = "bank"
    changed_pictures: dict[str, str] = {}
    errors: list[str] = []
    try:
        for width, height in VIEWPORTS.items():
            context = browser.new_context(viewport={"width": width, "height": height}, bypass_csp=True,
                                          color_scheme="light", reduced_motion="reduce")
            context.route("http://testserver/**", serve)
            page = context.new_page()
            page.on("pageerror", lambda error, w=width: errors.append(f"{w}px {page.url}: {error}"))
            for name, path, who in PAGES:
                limiter._hits.clear()  # ~50 pages a width, each with its scripts: past the per-minute limit
                if who != signed_in:
                    _sign_in(client, slug, people[who])
                    signed_in = who
                response = page.goto("http://testserver" + path.format(**paths).split("#")[0])
                assert response.status == 200 and "/ui/login" not in page.url, f"{name}: {response.status} at {page.url}"
                if name in STEPS:
                    click, wait_for = STEPS[name]
                    page.click(click)
                    page.wait_for_selector(wait_for)
                measured = page.evaluate(MEASURE, {"width": width, "touch": TOUCH_TARGET if width < 1024 else 0,
                                                   "decision": name == "exception"})
                page.add_script_tag(content=axe)
                results = page.evaluate("axe.run(document, {runOnly: {type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']}})")
                found[name][str(width)] = sorted(set(measured["problems"])) + _axe_problems(results)
                details[name][str(width)] = measured["details"]

                if os.environ.get("OPENCLEAR_VIEWPORT_SHOTS"):
                    SHOTS.mkdir(parents=True, exist_ok=True)
                    page.screenshot(path=SHOTS / f"{name}-{width}.png", full_page=True, animations="disabled", caret="hide")
                if width == REFERENCE_WIDTH:
                    page.evaluate(STEADY)
                    taken = page.screenshot(full_page=True, animations="disabled", caret="hide")
                    reference = SCREENSHOTS / f"{name}.png"
                    if writing:
                        SCREENSHOTS.mkdir(parents=True, exist_ok=True)
                        if not (reference.exists() and _identical(reference, taken)):  # PNG bytes vary when pixels don't
                            reference.write_bytes(taken)
                    elif reference.exists() and db_session.get_bind().dialect.name == "sqlite":
                        difference = _same_picture(reference, taken, DIFFS / f"{name}.png")
                        if difference:
                            changed_pictures[name] = difference
            context.close()
    finally:
        browser.close()
        runner.stop()

    if writing:
        BASELINE.write_text(json.dumps(found, indent=2, sort_keys=True, ensure_ascii=False) + "\n")
        _write_report(found, details)
        return
    assert not errors, "Script errors:\n" + "\n".join(errors)
    baseline = json.loads(BASELINE.read_text()) if BASELINE.exists() else {}
    new = {f"{name} at {width}px": sorted(set(problems) - set(baseline.get(name, {}).get(width, [])))
           for name, by_width in found.items() for width, problems in by_width.items()}
    new = {where: problems for where, problems in new.items() if problems}
    assert not new, "New problems on phones, tablets or desktops (see tests/fixtures/viewport/README.md):\n" + json.dumps(new, indent=2, ensure_ascii=False)
    assert not changed_pictures, (f"The desktop ({REFERENCE_WIDTH}px) changed; the new pictures and differences are in {DIFFS}:\n"
                                  + json.dumps(changed_pictures, indent=2))
