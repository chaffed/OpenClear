# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import re

from openclear.config import get_settings
from tests.conftest import TenantFactory

_NONCE_RE = re.compile(r"'nonce-([A-Za-z0-9_-]+)'")


def test_every_response_gets_the_baseline_security_headers(client):
    resp = client.get("/health")

    assert "Content-Security-Policy" in resp.headers
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
    assert resp.headers["X-Frame-Options"] == "DENY"
    assert resp.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
    assert "publickey-credentials-get=(self)" in resp.headers["Permissions-Policy"]


def test_headers_are_present_even_on_an_error_response(client, tenant_factory, monkeypatch):
    # main.py registers the security-headers middleware last (outermost), specifically so
    # it wraps every other middleware and still gets to add headers to a rejection they
    # produce -- confirmed here against the rate limiter's own 429.
    monkeypatch.setattr(get_settings(), "rate_limit_per_minute", 1)

    client.get("/health")
    resp = client.get("/health")

    assert resp.status_code == 429
    assert "Content-Security-Policy" in resp.headers
    assert resp.headers["X-Frame-Options"] == "DENY"


def test_csp_has_no_unsafe_inline_script_src(client):
    resp = client.get("/health")
    csp = resp.headers["Content-Security-Policy"]

    script_src = next(part for part in csp.split(";") if part.strip().startswith("script-src"))
    assert "'unsafe-inline'" not in script_src
    assert "'self'" in script_src
    assert _NONCE_RE.search(script_src)


def test_hsts_absent_by_default_dev_origin(client):
    # tests/conftest.py never overrides webauthn_origin, which defaults to
    # http://localhost:8000 -- sending Strict-Transport-Security here would be actively
    # wrong (it tells the browser to refuse future plain-HTTP connections).
    resp = client.get("/health")

    assert "Strict-Transport-Security" not in resp.headers


def test_hsts_present_when_deployment_is_https(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "webauthn_origin", "https://openclear.example.com")

    resp = client.get("/health")

    assert "max-age=" in resp.headers["Strict-Transport-Security"]
    assert "includeSubDomains" in resp.headers["Strict-Transport-Security"]


def test_inline_webauthn_script_is_nonced_and_matches_the_response_csp(client, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="csp-webauthn-nonce")
    client.get("/ui/login")
    csrf = client.cookies.get("csrf_token")
    client.post(
        "/ui/login",
        data={"tenant_slug": tenant.slug, "email": users["admin"].email, "password": TenantFactory.PASSWORD, "csrf_token": csrf, "next": "/ui/"},
    )

    resp = client.get("/ui/security")
    csp_nonce = _NONCE_RE.search(resp.headers["Content-Security-Policy"]).group(1)

    assert f'nonce="{csp_nonce}"' in resp.text
    # And the inline handlers this app used to rely on are gone from every rendered page --
    # a strict script-src with no 'unsafe-inline' would silently no-op them in a real
    # browser if they were still there.
    assert "onchange=" not in resp.text
    assert "onclick=" not in resp.text


def test_theme_selector_no_longer_uses_an_inline_handler(client):
    resp = client.get("/ui/login")

    assert "onchange=" not in resp.text
