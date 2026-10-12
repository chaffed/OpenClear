# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""The control panel (UX_PLAN.md U11; web/control_panel.py, routers/tenant_settings.py):
tiles by permission with their state, each setting at its own URL as a page or a modal's
content, the customer's own panel, the check return reasons tile, and (in a real browser)
the modal's behaviour: its URL, Back, Escape with unsaved changes, and saving in place."""

import pytest
from sqlalchemy import select

from openclear.domain.check_return_reason import CheckReturnReason
from openclear.domain.customer import Customer
from openclear.domain.tenant import Tenant
from openclear.services import customer_service, security_group_service, user_service
from tests.conftest import TenantFactory


def _login(client, slug, email):
    client.get("/ui/login")
    csrf = client.cookies.get("csrf_token")
    client.post("/ui/login", data={"tenant_slug": slug, "email": email, "password": TenantFactory.PASSWORD, "csrf_token": csrf, "next": "/ui/"})
    return csrf


def _keys(html: str) -> set[str]:
    import re

    return set(re.findall(r'class="tile" href="[^"]*" data-key="([^"]+)"', html))


def test_an_admin_sees_every_section_with_states(client, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="panel-admin")
    _login(client, tenant.slug, users["admin"].email)
    page = client.get("/ui/settings").text
    for section in ("Organization", "Security &amp; access", "Decisions &amp; rules", "Integrations", "Data &amp; compliance"):
        assert f">{section}</h2>" in page
    keys = _keys(page)
    assert {"branding", "dual-control", "reminders", "security-events", "check-return-reasons", "ml-models", "controls",
            "users", "sso", "webhooks"} <= keys
    assert "2 hours and 30 minutes before the cutoff" in page  # the reminders tile's state
    assert 'data-key="dual-control" data-title="Dual control" data-modal' in page
    assert 'data-state-url="/ui/admin/controls/summary"' in page
    assert 'data-key="sso" data-title="Single sign-on" aria' not in page  # a page, not a modal


def test_tiles_follow_permissions(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="panel-perms")
    group = security_group_service.create_security_group(
        db_session, tenant.id, security_group_service.SecurityGroupInput(name="Integrations only", permissions=["api_client:manage"]))
    user = user_service.create_user_with_membership(db_session, tenant.id, email="integrations@panel.example",
                                                    password=TenantFactory.PASSWORD, security_group_id=group.id)
    db_session.commit()
    _login(client, tenant.slug, user.email)
    assert _keys(client.get("/ui/settings").text) == {"api-clients", "webhooks", "integration"}
    assert client.get("/ui/settings/branding", follow_redirects=False).status_code == 403

    client.cookies.clear()
    _login(client, tenant.slug, users["viewer"].email)
    assert client.get("/ui/settings", follow_redirects=False).status_code == 403
    assert 'href="/ui/settings"' not in client.get("/ui/").text


def test_each_setting_is_a_page_or_a_modals_content(client, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="panel-sections")
    _login(client, tenant.slug, users["admin"].email)
    for key in ("branding", "contact", "messages", "password-policy", "timeouts", "dual-control", "reminders", "embedding",
                "decision-files", "security-events", "auto-import"):
        page = client.get(f"/ui/settings/{key}")
        assert page.status_code == 200, key
        assert "<html" in page.text and 'href="/ui/settings?from=' + key in page.text  # Back to settings, onto its tile
        partial = client.get(f"/ui/settings/{key}?partial=1")
        assert partial.status_code == 200 and "<html" not in partial.text and 'id="settings-section"' in partial.text, key


def test_saving_returns_to_the_panel_and_an_error_shows_with_the_form(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="panel-save")
    csrf = _login(client, tenant.slug, users["admin"].email)
    resp = client.post("/ui/settings/dual-control", data={"csrf_token": csrf, "require_dual_control": "true"}, follow_redirects=False)
    assert resp.status_code == 303 and resp.headers["location"].startswith("/ui/settings?flash=") and "from=dual-control" in resp.headers["location"]
    # The panel's script saves in the background and gets the outcome as JSON.
    resp = client.post("/ui/settings/dual-control", data={"csrf_token": csrf, "require_dual_control": "true"},
                       headers={"X-Requested-With": "fetch"})
    assert resp.json() == {"saved": True, "message": "Settings updated.", "key": "dual-control"}
    assert client.get("/ui/settings/tile-states").json()["dual-control"] == {"state": "On", "tone": "ok"}

    resp = client.post("/ui/settings/session-timeout", data={"csrf_token": csrf, "access_token_expire_minutes": "soon"})
    assert resp.status_code == 422 and 'id="settings-error"' in resp.text and "not a whole number" in resp.text


def test_a_bank_sets_its_reminders(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="panel-reminders")
    csrf = _login(client, tenant.slug, users["admin"].email)
    resp = client.post("/ui/settings/reminders", data={"csrf_token": csrf, "mode": "custom", "offsets": "2h, 15"}, follow_redirects=False)
    assert resp.status_code == 303
    db_session.expire_all()
    assert db_session.get(Tenant, tenant.id).reminder_offsets_minutes == "120,15"
    assert "2 hours and 15 minutes before the cutoff" in client.get("/ui/settings").text

    resp = client.post("/ui/settings/reminders", data={"csrf_token": csrf, "mode": "custom", "offsets": "tomorrow"})
    assert resp.status_code == 422 and "isn&#39;t a number" in resp.text and 'value="tomorrow"' in resp.text


def test_a_customers_own_panel(client, db_session, tenant_factory):
    tenant, _account, _users = tenant_factory.make(slug="panel-customer")
    customer = customer_service.create_customer(db_session, tenant.id, customer_service.CustomerInput(customer_number="P-1", name="Panel Co"))
    admin_group = security_group_service.get_security_group_by_name(db_session, tenant.id, "Admin")
    user_service.create_user_with_membership(db_session, tenant.id, email="owner@panelco.example", password=TenantFactory.PASSWORD,
                                             security_group_id=admin_group.id, customer_id=customer.id)
    db_session.commit()
    csrf = _login(client, tenant.slug, "owner@panelco.example")

    page = client.get("/ui/settings").text
    assert _keys(page) == {"reminders", "banner", "billing-statement"}
    assert "(your bank&#39;s)" in page
    form = client.get("/ui/settings/reminders").text
    assert "Use your bank's timing" in form and "platform default" not in form

    resp = client.post("/ui/settings/reminders", data={"csrf_token": csrf, "mode": "off"}, follow_redirects=False)
    assert resp.status_code == 303
    db_session.expire_all()
    assert db_session.get(Customer, customer.id).reminder_offsets_minutes == ""
    assert db_session.get(Tenant, tenant.id).reminder_offsets_minutes is None  # the bank's own is untouched
    assert client.post("/ui/settings/dual-control", data={"csrf_token": csrf}, follow_redirects=False).status_code == 403


def test_pages_opened_from_a_tile_lead_back_to_it(client, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="panel-back")
    _login(client, tenant.slug, users["admin"].email)
    for path, key in (("/ui/users", "users"), ("/ui/admin/webhooks", "webhooks"), ("/ui/admin/ml-models", "ml-models"),
                      ("/ui/check-return-reasons", "check-return-reasons")):
        assert f'href="/ui/settings?from={key}"' in client.get(path).text, path
    resp = client.get("/ui/admin", follow_redirects=False)
    assert resp.status_code == 303 and resp.headers["location"] == "/ui/settings"


def test_the_controls_tile_state(client, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="panel-controls")
    _login(client, tenant.slug, users["admin"].email)
    state = client.get("/ui/admin/controls/summary").json()
    assert state["tone"] in ("ok", "warn", "danger") and ("FAIL" in state["state"] or "WARN" in state["state"] or state["state"] == "All pass")


def test_check_return_reasons_can_be_added_edited_and_retired(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="panel-returns")
    csrf = _login(client, tenant.slug, users["admin"].email)
    assert "Refer to maker" in client.get("/ui/check-return-reasons").text

    resp = client.post("/ui/check-return-reasons", data={"csrf_token": csrf, "reason_text": "Endorsement missing", "return_code": "j"},
                       follow_redirects=False)
    assert resp.status_code == 303
    reason = db_session.execute(select(CheckReturnReason).where(CheckReturnReason.tenant_id == tenant.id,
                                                                CheckReturnReason.reason_text == "Endorsement missing")).scalar_one()
    assert reason.return_code == "J"

    bad = client.post(f"/ui/check-return-reasons/{reason.id}", data={"csrf_token": csrf, "reason_text": "Endorsement missing", "return_code": "TOO-LONG"})
    assert bad.status_code == 422

    client.post(f"/ui/check-return-reasons/{reason.id}/deactivate", data={"csrf_token": csrf})
    db_session.refresh(reason)
    assert reason.is_active is False
    client.post(f"/ui/check-return-reasons/{reason.id}/reactivate", data={"csrf_token": csrf})
    db_session.refresh(reason)
    assert reason.is_active is True

    client.cookies.clear()
    _login(client, tenant.slug, users["viewer"].email)
    assert client.get("/ui/check-return-reasons", follow_redirects=False).status_code == 403


# --- In a real browser ---------------------------------------------------------------------------


def test_the_modal_in_a_browser(client, tenant_factory):
    playwright = pytest.importorskip("playwright.sync_api")
    try:
        runner = playwright.sync_playwright().start()
        browser = runner.chromium.launch()
    except Exception as exc:  # noqa: BLE001 -- no browser installed here
        pytest.skip(f"Chromium isn't available: {exc}")

    tenant, _account, users = tenant_factory.make(slug="panel-browser")
    _login(client, tenant.slug, users["admin"].email)

    def serve(route):
        request = route.request
        url = request.url.replace("http://testserver", "")
        response = client.request(request.method, url, headers={k: v for k, v in request.headers.items() if k.lower() != "cookie"},
                                  content=request.post_data_buffer, follow_redirects=False)
        headers = dict(response.headers)
        route.fulfill(status=response.status_code, headers=headers, body=response.content)

    try:
        context = browser.new_context()
        context.route("http://testserver/**", serve)
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(error))
        page.goto("http://testserver/ui/settings")

        # The search filters the tiles and hides empty sections.
        page.fill("[data-panel-search]", "dual")
        assert page.locator("[data-panel-tile]:visible").count() == 1
        page.fill("[data-panel-search]", "")

        # A modal tile opens at its own URL; Back closes it, focus returns to the tile.
        page.click('.tile[data-key="dual-control"]')
        page.wait_for_selector("#settings-dialog[open] #settings-section")
        assert page.url.endswith("/ui/settings/dual-control")
        assert page.inner_text("#settings-dialog-title") == "Dual control"
        page.go_back()
        page.wait_for_selector("#settings-dialog:not([open])", state="attached")
        assert page.url.endswith("/ui/settings")
        assert page.evaluate("document.activeElement.dataset.key") == "dual-control"

        # Escape with unsaved changes asks first; keeping them leaves it open.
        page.click('.tile[data-key="dual-control"]')
        page.wait_for_selector("#settings-dialog[open] #settings-section")
        page.check('#settings-dialog input[name="require_dual_control"]')
        page.keyboard.press("Escape")
        page.wait_for_selector("#confirm-dialog[open]")
        page.click("#confirm-dialog [data-confirm-no]")
        assert page.locator("#settings-dialog[open]").count() == 1

        # Saving closes it, updates the tile and says so.
        page.click('#settings-dialog button[type="submit"]')
        page.wait_for_selector("#settings-dialog:not([open])", state="attached")
        page.wait_for_function("document.querySelector('.tile[data-key=\"dual-control\"] [data-tile-state]').textContent === 'On'")
        assert page.url.endswith("/ui/settings")
        assert "Settings updated." in page.inner_text("#toast-region")

        # An error comes back inside the dialog.
        page.click('.tile[data-key="reminders"]')
        page.wait_for_selector("#settings-dialog[open] #settings-section")
        page.check('#settings-dialog input[name="mode"][value="custom"]')
        page.fill('#settings-dialog input[name="offsets"]', "whenever")
        page.click('#settings-dialog button[type="submit"]')
        page.wait_for_selector("#settings-dialog #settings-error")
        assert "isn't a number" in page.inner_text("#settings-dialog #settings-error")

        # A page tile is an ordinary page with its way back.
        page.goto("http://testserver/ui/settings/dual-control")
        assert page.locator("#settings-dialog").count() == 0 and page.locator("text=Back to settings").count() == 1
        assert not errors
    finally:
        browser.close()
        runner.stop()
