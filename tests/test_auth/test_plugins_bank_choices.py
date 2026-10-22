# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Plugins PL2 (PLUGIN_PLAN.md): the example plugin in examples/ end to end. A plugin's
import format and decision layout are off for each bank until its admin turns them on;
then files in that format import like any other, and decision files come out in that
layout."""

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from openclear import plugins
from openclear.config import get_settings
from openclear.domain.audit_log_entry import AuditLogEntry
from openclear.domain.decision import DecisionOutcome
from openclear.domain.paid_item import PaidItem
from openclear.services import plugin_service
from tests.conftest import TenantFactory, login_headers
from tests.items import _decide, _make_exception

EXAMPLE = Path(__file__).resolve().parents[2] / "examples" / "openclear-plugin-example" / "src"


class _EntryPoint:
    name, value, dist = "example", "openclear_plugin_example:plugin", SimpleNamespace(name="openclear-plugin-example")

    def load(self):
        import openclear_plugin_example

        return openclear_plugin_example.plugin


@pytest.fixture
def example_plugin(monkeypatch, engine):
    """The example plugin installed and allowed, as an operator would (after `engine`, which
    resets settings on Postgres)."""
    monkeypatch.syspath_prepend(str(EXAMPLE))
    monkeypatch.setattr(plugins, "entry_points", lambda group: [_EntryPoint()] if group == plugins.ENTRY_POINT_GROUP else [])
    monkeypatch.setattr(get_settings(), "plugins", "example")
    plugins.reset_for_tests()
    plugins.load()
    yield
    plugins.reset_for_tests()
    sys.modules.pop("openclear_plugin_example", None)


def _line(account, check, cents, date="20260110"):
    return f"{account:<17}{check:<10}{cents:012d}{date}"


def _upload(client, headers, content: str):
    return client.post("/api/v1/bulk-uploads", headers=headers, data={"kind": "paid_items", "format": "example_fixed_width"},
                       files={"file": ("presented.txt", content.encode(), "text/plain")})


def _web_login(client, tenant, user):
    client.get("/ui/login")
    csrf = client.cookies.get("csrf_token")
    client.post("/ui/login", data={"tenant_slug": tenant.slug, "email": user.email, "password": TenantFactory.PASSWORD,
                                   "csrf_token": csrf, "next": "/ui/"})
    return csrf


def test_off_until_the_bank_turns_it_on(example_plugin, client, db_session, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="plug-off")
    headers = login_headers(client, tenant.slug, users["admin"].email)

    formats = client.get("/api/v1/bulk-uploads/formats", headers=headers).json()
    assert {"kind": "paid_items", "format": "tabular", "built_in": True} in formats
    assert not any(f["format"] == "example_fixed_width" for f in formats)
    refused = _upload(client, headers, _line(account.account_number, "1001", 12345) + "\n")
    assert refused.status_code == 422 and "isn't turned on" in refused.json()["detail"]
    assert client.get("/api/v1/decisions/file?layout=example_fixed_width", headers=headers).status_code == 422
    assert client.get("/api/v1/decisions/file", headers=headers).status_code == 200  # the built-in CSV is always on


def test_turned_on_a_file_imports_and_decisions_come_back_in_the_layout(example_plugin, client, db_session, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="plug-on")
    other, _other_account, other_users = tenant_factory.make(slug="plug-on-other")
    csrf = _web_login(client, tenant, users["admin"])

    page = client.get("/ui/admin/plugins")
    assert "paid_items/example_fixed_width" in page.text and "Turn on" in page.text
    for point, name in (("import_formats", "paid_items/example_fixed_width"), ("decision_layouts", "example_fixed_width")):
        resp = client.post("/ui/admin/plugins/choices", data={"csrf_token": csrf, "point": point, "name": name, "enabled": "true"},
                           follow_redirects=False)
        assert resp.status_code == 303
    audit = db_session.execute(select(AuditLogEntry).where(AuditLogEntry.tenant_id == tenant.id, AuditLogEntry.action == "plugin.enable")).scalars().all()
    assert len(audit) == 2
    assert "paid items: example_fixed_width" in client.get("/ui/bulk-uploads/new").text

    headers = login_headers(client, tenant.slug, users["admin"].email)
    content = _line(account.account_number, "2001", 12345) + "\n" + _line(account.account_number, "2002", 500) + "\n"
    accepted = _upload(client, headers, content)
    assert accepted.status_code == 202, accepted.text
    status = client.get(accepted.json()["status_url"], headers=headers).json()
    assert (status["status"], status["succeeded"], status["failed"]) == ("completed", 2, 0), status
    amounts = {p.check_number: str(p.presented_amount) for p in db_session.execute(select(PaidItem).where(PaidItem.tenant_id == tenant.id)).scalars()}
    assert amounts == {"2001": "123.45", "2002": "5.00"}

    exception = _make_exception(db_session, tenant, account, users, "3001", "100.00", "999.00")
    _decide(db_session, tenant, users, exception, DecisionOutcome.RETURN)
    file = client.get("/api/v1/decisions/file?layout=example_fixed_width", headers=headers)
    assert file.status_code == 200 and file.headers["content-disposition"].endswith('.txt"')
    [line] = file.text.splitlines()
    assert line.startswith("0000000001" + f"{account.account_number:<17}" + f"{'3001':<15}" + "000000099900R")

    # Another bank on the same installation hasn't turned anything on.
    other_headers = login_headers(client, other.slug, other_users["admin"].email)
    assert _upload(client, other_headers, content).status_code == 422
    assert plugin_service.is_enabled(db_session, other.id, "decision_layouts", "example_fixed_width") is False


def test_a_bad_line_fails_the_file_or_the_row(example_plugin, client, db_session, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="plug-bad")
    for point, name in (("import_formats", "paid_items/example_fixed_width"),):
        plugin_service.set_enabled(db_session, tenant.id, point, name, True, user_id=None)
    db_session.commit()
    headers = login_headers(client, tenant.slug, users["admin"].email)

    rejected = client.get(_upload(client, headers, "too short\n").json()["status_url"], headers=headers).json()
    assert rejected["status"] == "rejected" and "must be 47" in rejected["error"]
    mixed = _upload(client, headers, _line(account.account_number, "4001", 100) + "\n" + _line("NOPE", "4002", 100) + "\n")
    status = client.get(mixed.json()["status_url"], headers=headers).json()
    assert (status["succeeded"], status["failed"]) == (1, 1)


def test_only_plugin_entries_can_be_switched(example_plugin, db_session, tenant_factory):
    tenant, _account, _users = tenant_factory.make(slug="plug-switch")
    with pytest.raises(ValueError):
        plugin_service.set_enabled(db_session, tenant.id, "decision_layouts", "openclear_csv", False, user_id=None)
    with pytest.raises(ValueError):
        plugin_service.set_enabled(db_session, tenant.id, "decision_layouts", "nonexistent", True, user_id=None)
    assert plugin_service.set_enabled(db_session, tenant.id, "decision_layouts", "example_fixed_width", True, user_id=None)
    assert not plugin_service.set_enabled(db_session, tenant.id, "decision_layouts", "example_fixed_width", True, user_id=None)


def test_a_required_plugin_that_doesnt_load_stops_startup(monkeypatch):
    monkeypatch.setattr(plugins, "entry_points", lambda group: [])
    monkeypatch.setattr(get_settings(), "plugins_required", "example")
    plugins.reset_for_tests()
    try:
        with pytest.raises(plugins.PluginError, match="Required plugins didn't load: example"):
            plugins.load()
        monkeypatch.setattr(get_settings(), "plugins_required", "")
        assert any(p.name == "openclear" for p in plugins.load())  # nothing half-registered was left behind
    finally:
        plugins.reset_for_tests()
