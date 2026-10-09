# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Plugins (PLUGIN_PLAN.md PL1): discovery through entry points, the operator's allow-list,
API version checks, and a broken or greedy plugin being reported without stopping the app."""

from types import SimpleNamespace

import pytest

from openclear import plugins
from openclear.config import get_settings
from openclear.domain.bulk_upload_file import BulkUploadKind
from tests.conftest import TenantFactory


class _FakeEntryPoint:
    def __init__(self, name, obj, dist="openclear-example"):
        self.name, self._obj, self.dist, self.value = name, obj, SimpleNamespace(name=dist), f"{dist}:plugin"

    def load(self):
        if isinstance(self._obj, Exception):
            raise self._obj
        return self._obj


def _plugin(name, register, api=(1, 1)):
    return SimpleNamespace(name=name, version="0.1", plugin_api=api, register=register)


@pytest.fixture
def installed(monkeypatch):
    """Pretend these plugin packages are installed, then put the real registry back."""
    def install(*entry_points):
        monkeypatch.setattr(plugins, "entry_points", lambda group: list(entry_points) if group == plugins.ENTRY_POINT_GROUP else [])
        plugins.reset_for_tests()
        return plugins.load()

    yield install
    plugins.reset_for_tests()


def _by_name(status):
    return {p.name: p for p in status}


def test_built_ins_register_through_the_same_interface():
    status = _by_name(plugins.status())
    builtin = status["openclear"]
    assert builtin.status == "loaded" and builtin.source == "built-in"
    assert {"networks: check", "networks: ach", "ocr_providers: tesseract", "storage_backends: s3", "scoring_models: logistic_regression",
            "import_formats: paid_items/tabular"} <= set(builtin.provides)


def test_an_allowed_plugin_extends_the_app(installed, monkeypatch):
    monkeypatch.setattr(get_settings(), "plugins", "fancy")
    sentinel = object()

    def register(registry):
        registry.add_ocr_provider("fancy_ocr", lambda: sentinel)
        registry.add_import_format(BulkUploadKind.PAID_ITEMS, "fancy_core_layout", "SPEC")

    status = _by_name(installed(_FakeEntryPoint("fancy", _plugin("fancy", register))))
    assert status["fancy"].status == "loaded" and status["fancy"].source == "openclear-example"
    assert plugins.lookup("ocr_providers", "fancy_ocr", what="OCR provider")() is sentinel
    from openclear.services import bulk_import_service

    assert bulk_import_service.spec_for(BulkUploadKind.PAID_ITEMS, "fancy_core_layout") == "SPEC"


def test_installed_is_not_enough(installed, monkeypatch):
    monkeypatch.setattr(get_settings(), "plugins", "")
    status = _by_name(installed(_FakeEntryPoint("sneaky", _plugin("sneaky", lambda r: r.add_ocr_provider("sneaky", object)))))
    assert status["sneaky"].status == "not allowed"
    with pytest.raises(ValueError, match="Unknown OCR provider"):
        plugins.lookup("ocr_providers", "sneaky", what="OCR provider")


def test_incompatible_broken_and_greedy_plugins_are_reported_and_skipped(installed, monkeypatch):
    monkeypatch.setattr(get_settings(), "plugins", "old,broken,missing,greedy")

    def boom(registry):
        raise RuntimeError("bad config")

    def _greedy(registry):
        registry.add_ocr_provider("greedy_ocr", object)  # fine on its own...
        registry.add_storage_backend("s3", object)  # ...but this one's taken

    status = _by_name(installed(
        _FakeEntryPoint("old", _plugin("old", lambda r: None, api=(0, 0))),
        _FakeEntryPoint("broken", _plugin("broken", boom)),
        _FakeEntryPoint("missing", ModuleNotFoundError("No module named 'openclear_missing'")),
        _FakeEntryPoint("greedy", _plugin("greedy", _greedy)),
    ))
    assert status["old"].status == "incompatible"
    assert status["broken"].status == "failed" and "bad config" in status["broken"].error
    assert status["missing"].status == "failed"
    assert status["greedy"].status == "failed" and "already has 's3'" in status["greedy"].error
    assert status["openclear"].status == "loaded"  # the app still has everything it needs
    plugins.lookup("storage_backends", "s3", what="storage backend")
    with pytest.raises(ValueError):  # a failed plugin leaves nothing behind
        plugins.lookup("ocr_providers", "greedy_ocr", what="OCR provider")
    assert status["greedy"].provides == []


def test_admins_see_the_plugins_page(client, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="plugins-page")
    client.get("/ui/login")
    csrf = client.cookies.get("csrf_token")
    client.post("/ui/login", data={"tenant_slug": tenant.slug, "email": users["admin"].email, "password": TenantFactory.PASSWORD,
                                   "csrf_token": csrf, "next": "/ui/"})
    page = client.get("/ui/admin/plugins")
    assert page.status_code == 200 and "networks: check" in page.text and "built-in" in page.text
