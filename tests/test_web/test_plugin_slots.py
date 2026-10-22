# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Plugin UI slots (MOBILE_PLAN.md R5; plugins/ui_slots.py): off for each bank until it turns
them on, sanitised, never able to break a page, scoped to the person, and the example plugin's
page additions end to end."""

import logging
import sys
import time
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from openclear import plugins
from openclear.config import get_settings
from openclear.domain.audit_log_entry import AuditLogEntry
from openclear.plugins.api import SlotAction, SlotContent, UiSlot
from openclear.services import plugin_service, security_group_service, user_service
from tests.conftest import TenantFactory
from tests.test_auth.test_plugins_bank_choices import EXAMPLE, _EntryPoint
from tests.items import _make_customer_with_account, _make_exception
from tests.test_web.test_mobile_components import _sign_in, browser_page  # noqa: F401 -- a fixture

TEST_SLOTS: list[UiSlot] = []


class _TestPlugin:
    name, version, plugin_api = "testslots", "1", (1, 1)

    def register(self, registry):
        for entry in TEST_SLOTS:
            registry.add_ui_slot(entry)


class _TestEntryPoint:
    name, value, dist = "testslots", "tests:testslots", SimpleNamespace(name="testslots")

    def load(self):
        return _TestPlugin()


@pytest.fixture
def slot_plugins(monkeypatch, engine):
    """The example plugin and a test plugin (its slots set by each test in TEST_SLOTS), installed
    and allowed before the app is made, as an operator would."""
    monkeypatch.syspath_prepend(str(EXAMPLE))
    monkeypatch.setattr(plugins, "entry_points",
                        lambda group: [_EntryPoint(), _TestEntryPoint()] if group == plugins.ENTRY_POINT_GROUP else [])
    monkeypatch.setattr(get_settings(), "plugins", "example,testslots")
    TEST_SLOTS.clear()
    plugins.reset_for_tests()
    yield TEST_SLOTS
    plugins.reset_for_tests()
    TEST_SLOTS.clear()
    sys.modules.pop("openclear_plugin_example", None)
    sys.modules.pop("openclear_plugin_example.ui", None)


def _reload(slots, *entries):
    """The test plugin's slots, registered again (the app made by `client` loaded it empty)."""
    slots.extend(entries)
    plugins.reset_for_tests()
    plugins.load()


def _turn_on(db_session, tenant, *names):
    for name in names:
        plugin_service.set_enabled(db_session, tenant.id, "ui_slots", name, True, user_id=None)
    db_session.commit()


def _setup(db_session, tenant_factory, slug):
    tenant, account, users = tenant_factory.make(slug=slug)
    item = _make_exception(db_session, tenant, account, users, "7101", "10.00", "99.00")
    return tenant, account, users, item


def test_off_until_the_bank_turns_it_on(slot_plugins, client, db_session, tenant_factory):
    tenant, _account, users, item = _setup(db_session, tenant_factory, "slot-off")
    _sign_in(client, tenant, users)
    page = client.get(f"/ui/exceptions/{item.id}").text
    assert "Vendor score" not in page and "/static/plugins/example/" not in page
    assert client.get("/ui/plugin-pages/example_report").status_code == 404
    assert client.get(f"/ui/plugins/example/lookup?exception={item.id}").status_code == 404  # its routes too

    _turn_on(db_session, tenant, "example_vendor_score")
    page = client.get(f"/ui/exceptions/{item.id}").text
    assert '<details class="card more-details plugin-panel"' in page and "Vendor score" in page
    assert '<span class="example-score">' in page and f"/ui/plugins/example/exceptions/{item.id}/refresh" in page
    assert '<script src="/static/plugins/example/example.js"></script>' in page
    assert '<link rel="stylesheet" href="/static/plugins/example/example.css">' in page
    assert "/static/plugins/example/" not in client.get("/ui/paid-items").text  # only where its slot rendered
    assert client.get("/static/plugins/example/example.js").status_code == 200


def test_the_example_plugin_fills_each_kind(slot_plugins, client, db_session, tenant_factory):
    tenant, _account, users, item = _setup(db_session, tenant_factory, "slot-kinds")
    _turn_on(db_session, tenant, "example_vendor_lookup", "example_vendor_export", "example_report", "example_tile")
    csrf = _sign_in(client, tenant, users)
    queue = client.get("/ui/exceptions").text
    assert 'class="plugin-toolbar"' in queue and 'href="/ui/plugins/example/export"' in queue
    assert 'class="plugin-row-actions"' in queue and f'href="/ui/plugins/example/lookup?exception={item.id}"' in queue
    assert 'href="/ui/plugin-pages/example_report"' in queue  # its menu entry

    report = client.get("/ui/plugin-pages/example_report")
    assert report.status_code == 200 and "<h1>Vendor score report</h1>" in report.text
    lookup = client.get(f"/ui/plugins/example/lookup?exception={item.id}")
    assert lookup.status_code == 200 and "Its score is" in lookup.text
    assert client.get("/ui/plugins/example/export").headers["content-type"].startswith("text/csv")
    panel = client.get("/ui/settings").text
    assert "Example vendor" in panel and "Connected" in panel

    # Its action: CSRF checked, recorded in the action log.
    assert client.post(f"/ui/plugins/example/exceptions/{item.id}/refresh", data={}, follow_redirects=False).status_code == 403
    done = client.post(f"/ui/plugins/example/exceptions/{item.id}/refresh", data={"csrf_token": csrf}, follow_redirects=False)
    assert done.status_code == 303
    db_session.expire_all()
    assert db_session.execute(select(AuditLogEntry).where(AuditLogEntry.tenant_id == tenant.id,
                                                          AuditLogEntry.action == "example.score_refreshed")).scalar_one()


def test_a_slots_html_is_sanitised(slot_plugins, client, db_session, tenant_factory):
    _reload(slot_plugins, UiSlot("test_dirty", "dashboard.panel", lambda c: SlotContent(
        title="Dirty <b>title</b>",
        html='<p onclick="steal()">Hello</p><script>steal()</script><a href="javascript:steal()">x</a>'
             '<img src="x" onerror="steal()"><style>body{}</style><span class="ok">kept</span>')))
    tenant, _account, users, _item = _setup(db_session, tenant_factory, "slot-clean")
    _turn_on(db_session, tenant, "test_dirty")
    _sign_in(client, tenant, users)
    page = client.get("/ui/").text
    panel = page[page.index('data-plugin-slot="dashboard.panel"'):]
    assert "<p>Hello</p>" in panel and '<span class="ok">kept</span>' in panel
    assert "steal()" not in panel and "<script>steal" not in panel and "<style>" not in panel
    assert "Dirty &lt;b&gt;title&lt;/b&gt;" in panel  # titles are escaped, not HTML


def test_a_failing_slow_or_malformed_slot_never_breaks_the_page(slot_plugins, client, db_session, tenant_factory, caplog):
    def boom(context):
        raise RuntimeError("vendor down")

    def slow(context):
        time.sleep(1.0)
        return SlotContent(title="Too late")

    _reload(slot_plugins,
            UiSlot("test_boom", "dashboard.panel", boom),
            UiSlot("test_slow", "dashboard.panel", slow),
            UiSlot("test_offsite", "dashboard.panel", lambda c: SlotContent(title="Offsite", actions=(SlotAction("Go", "https://evil.example"),))),
            UiSlot("test_fine", "dashboard.panel", lambda c: SlotContent(title="Still here")))
    tenant, _account, users, _item = _setup(db_session, tenant_factory, "slot-safe")
    _turn_on(db_session, tenant, "test_boom", "test_slow", "test_offsite", "test_fine")
    _sign_in(client, tenant, users)
    caplog.set_level(logging.WARNING, logger="openclear.plugins.ui_slots")
    started = time.monotonic()
    response = client.get("/ui/")
    assert response.status_code == 200 and time.monotonic() - started < 1.0
    assert "Still here" in response.text
    assert "Too late" not in response.text and "Offsite" not in response.text
    reasons = {(r.slot_name, r.reason) for r in caplog.records if getattr(r, "event", "") == "plugin.slot.failed"}
    assert reasons == {("test_boom", "error"), ("test_slow", "slow"), ("test_offsite", "malformed")}


def test_a_slot_needs_its_permission_and_sees_the_persons_scope(slot_plugins, client, db_session, tenant_factory):
    from openclear.db import account_scope

    seen = {}

    def scoped(context):
        seen["accounts"] = account_scope.account_ids(context.session)
        seen["customer"] = context.ctx.customer_id
        return SlotContent(title="Scoped")

    _reload(slot_plugins,
            UiSlot("test_admins", "exceptions.toolbar", lambda c: SlotContent(title="A", actions=(SlotAction("Admins only", "/ui/"),)),
                   permission="admin:manage"),
            UiSlot("test_scope", "dashboard.panel", scoped))
    tenant, _account, users, _item = _setup(db_session, tenant_factory, "slot-scope")
    _turn_on(db_session, tenant, "test_admins", "test_scope")

    _sign_in(client, tenant, users)
    assert "Admins only" in client.get("/ui/exceptions").text
    client.cookies.clear()
    client.get("/ui/login")
    client.post("/ui/login", data={"tenant_slug": tenant.slug, "email": users["viewer"].email, "password": TenantFactory.PASSWORD,
                                   "csrf_token": client.cookies.get("csrf_token"), "next": "/ui/"})
    assert "Admins only" not in client.get("/ui/exceptions").text

    # A customer's person limited to one account: the slot's own session carries that limit.
    from openclear.services import account_access_service

    customer, limited = _make_customer_with_account(db_session, tenant, "SL-1")
    group = next(g for g in security_group_service.list_security_groups(db_session, tenant.id) if g.name == "Viewer")
    person = user_service.create_user_with_membership(db_session, tenant.id, email="pat@slots.example.com", password=TenantFactory.PASSWORD,
                                                      security_group_id=group.id, customer_id=customer.id)
    db_session.flush()
    from openclear.domain.tenant_membership import TenantMembership

    membership = db_session.execute(select(TenantMembership).where(TenantMembership.user_id == person.id)).scalar_one()
    account_access_service.set_access(db_session, tenant.id, membership.id, all_accounts=False, account_ids=[limited.id])
    db_session.commit()
    client.cookies.clear()
    client.get("/ui/login")
    client.post("/ui/login", data={"tenant_slug": tenant.slug, "email": "pat@slots.example.com", "password": TenantFactory.PASSWORD,
                                   "csrf_token": client.cookies.get("csrf_token"), "next": "/ui/"})
    client.get("/ui/", follow_redirects=True)
    assert seen == {"accounts": (limited.id,), "customer": customer.id}


def test_a_panel_is_a_card_on_a_desktop_and_closed_on_a_phone(slot_plugins, client, db_session, tenant_factory, browser_page):  # noqa: F811
    tenant, _account, users, item = _setup(db_session, tenant_factory, "slot-phone")
    _turn_on(db_session, tenant, "example_vendor_score")
    _sign_in(client, tenant, users)
    for width, open_ in ((1280, True), (360, False)):
        page = browser_page(width, 800)
        page.goto(f"http://testserver/ui/exceptions/{item.id}")
        panel = page.locator("details.plugin-panel")
        assert panel.evaluate("el => el.open") is open_
        assert page.locator(".example-score[data-example-ready]").count() == 1  # the plugin's own script ran
