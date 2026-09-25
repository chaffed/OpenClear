# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Public demo guardrails (web/demo_guard.py) and the scheduled hourly reset
(workers/tasks.py::demo_reset_job) — FIX_PLAN.md Phase 4c."""

import importlib
import pkgutil
import re
import uuid

import pytest
from fastapi.routing import APIRoute

import openclear.api.v1 as api_v1
import openclear.web.routers as web_routers
from openclear.config import get_settings
from openclear.services import demo_tenant_service
from openclear.web.demo_guard import _LOCKED_PATTERNS, DEMO_LOCKED_MESSAGE, is_locked_in_demo
from openclear.workers import tasks
from tests.conftest import TenantFactory, login_headers

DEMO_PASSWORD = "DemoGuardTest123!"


def _state_changing_routes() -> list[tuple[str, str]]:
    """Every non-GET route, as a concrete (method, path) with sample ids filled in."""
    routes = []
    for package, base in ((web_routers, ""), (api_v1, "/api/v1")):
        for module_info in pkgutil.iter_modules(package.__path__):
            module = importlib.import_module(f"{package.__name__}.{module_info.name}")
            router = getattr(module, "router", None)
            for route in getattr(router, "routes", []):
                if isinstance(route, APIRoute):
                    path = base + re.sub(r"\{[^}]+\}", str(uuid.uuid4()), route.path)
                    routes.extend((method, path) for method in route.methods - {"GET", "HEAD"})
    return routes


def test_every_locked_pattern_matches_a_real_route():
    """Guards against a typo (or a renamed route) silently unlocking something."""
    routes = _state_changing_routes()
    assert len(routes) > 100
    for pattern in _LOCKED_PATTERNS:
        assert any(re.fullmatch(pattern, path) for _method, path in routes), f"{pattern!r} matches no route"


def test_the_everyday_workflow_is_not_locked():
    locked = {path for method, path in _state_changing_routes() if is_locked_in_demo(method, path)}
    for everyday in ("/ui/issued-items", "/ui/stop-payments", "/ui/paid-items", "/ui/settings/messages", "/ui/users",
                     "/ui/login", "/ui/logout", "/ui/auth/refresh", "/ui/theme", "/ui/admin/demo/reset"):
        assert everyday not in locked
    assert not any(re.fullmatch(r"/ui/exceptions/[^/]+/(decide|recommend)", path) for path in locked)
    assert not is_locked_in_demo("GET", "/ui/settings")


@pytest.fixture
def demo(db_session, monkeypatch):
    monkeypatch.setattr(get_settings(), "demo_tenant_password", DEMO_PASSWORD)
    return demo_tenant_service.ensure_demo_tenant(db_session)


def _login_demo(client, tenant):
    client.get("/ui/login")
    client.post("/ui/login", data={"tenant_slug": tenant.slug, "email": demo_tenant_service.DEMO_ADMIN_EMAIL,
                                   "password": DEMO_PASSWORD, "csrf_token": client.cookies.get("csrf_token")})


@pytest.mark.parametrize(
    "path",
    ["/ui/security-groups", "/ui/security/sign-out-others", "/ui/security/password", "/ui/settings",
     "/ui/settings/session-timeout", "/ui/admin/sso", "/ui/admin/ml/retrain", "/ui/settings/data-export/start"],
)
def test_locked_actions_are_refused_in_the_demo(client, demo, path):
    _login_demo(client, demo)

    resp = client.post(path, data={"csrf_token": client.cookies.get("csrf_token"), "name": "x"}, follow_redirects=False)

    assert resp.status_code == 403
    assert "turned off in the public demo" in resp.text.replace("&#39;", "'")
    assert "Turned off in the demo" in resp.text  # not the generic "no access" heading


def test_everyday_actions_still_work_in_the_demo(client, demo):
    _login_demo(client, demo)

    resp = client.post(
        "/ui/settings/messages",
        data={"csrf_token": client.cookies.get("csrf_token"), "login_message": "", "banner_message": "Hello visitors"},
        follow_redirects=False,
    )

    assert resp.status_code == 303


def test_same_actions_are_allowed_outside_the_demo(client, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="not-a-demo")
    client.get("/ui/login")
    client.post("/ui/login", data={"tenant_slug": tenant.slug, "email": users["admin"].email,
                                   "password": TenantFactory.PASSWORD, "csrf_token": client.cookies.get("csrf_token")})

    resp = client.post("/ui/security/sign-out-others", data={"csrf_token": client.cookies.get("csrf_token")}, follow_redirects=False)

    assert resp.status_code == 303


def test_locked_api_actions_are_refused_in_the_demo(client, demo):
    headers = login_headers(client, demo.slug, demo_tenant_service.DEMO_ADMIN_EMAIL, DEMO_PASSWORD)

    for method, path in (("POST", "/api/v1/auth/webauthn/register/options"), ("POST", "/api/v1/admin/ml/retrain")):
        resp = client.request(method, path, headers=headers, json={"network_code": "check"})
        assert resp.status_code == 403, path
        assert resp.json()["detail"] == DEMO_LOCKED_MESSAGE

    assert client.get("/api/v1/exceptions", headers=headers).status_code == 200


def test_demo_pages_say_it_is_a_demo(client, demo, tenant_factory):
    assert "public demo" in client.get(f"/ui/login/{demo.slug}").text
    _login_demo(client, demo)
    assert "Public demo." in client.get("/ui/exceptions").text

    other, _account, users = tenant_factory.make(slug="not-a-demo-notice")
    assert "public demo" not in client.get(f"/ui/login/{other.slug}").text


def test_scheduled_reset_undoes_visitor_changes(client, db_session, session_factory, demo, monkeypatch):
    from openclear.domain.tenant import Tenant

    _login_demo(client, demo)
    client.post("/ui/settings/messages", data={"csrf_token": client.cookies.get("csrf_token"),
                                                "login_message": "", "banner_message": "Defaced!"})
    db_session.expire_all()
    assert db_session.get(Tenant, demo.id).banner_message == "Defaced!"

    monkeypatch.setattr(tasks, "get_session_factory", lambda: session_factory)
    tasks.demo_reset_job()

    db_session.expire_all()
    assert db_session.get(Tenant, demo.id).banner_message != "Defaced!"
    assert client.get("/ui/exceptions", follow_redirects=False).status_code == 303  # visitors sign in again


def test_scheduled_reset_is_a_no_op_without_a_demo(session_factory, monkeypatch):
    monkeypatch.setattr(tasks, "get_session_factory", lambda: session_factory)
    tasks.demo_reset_job()  # must not raise


def test_scheduler_registers_the_reset_only_when_the_demo_is_enabled(monkeypatch):
    from openclear.workers import scheduler

    monkeypatch.setattr(get_settings(), "demo_tenant_enabled", True)
    assert scheduler.demo_reset_enabled(get_settings())
    monkeypatch.setattr(get_settings(), "demo_tenant_reset_interval_minutes", 0)
    assert not scheduler.demo_reset_enabled(get_settings())
    monkeypatch.setattr(get_settings(), "demo_tenant_enabled", False)
    monkeypatch.setattr(get_settings(), "demo_tenant_reset_interval_minutes", 60)
    assert not scheduler.demo_reset_enabled(get_settings())
