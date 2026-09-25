# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import base64
import io

from PIL import Image

from openclear.config import get_settings
from tests.conftest import TenantFactory


def _login(client, tenant_slug, email, password=TenantFactory.PASSWORD):
    client.get("/ui/login")
    csrf = client.cookies.get("csrf_token")
    client.post(
        "/ui/login", data={"tenant_slug": tenant_slug, "email": email, "password": password, "csrf_token": csrf, "next": "/ui/"}
    )
    return csrf


def _png_data_uri():
    image = Image.new("RGB", (10, 10), color="red")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return f"data:image/png;base64,{base64.b64encode(buffer.getvalue()).decode()}"


def test_preview_requires_login(client):
    resp = client.post("/ui/markdown-preview", data={"text": "hello"}, follow_redirects=False)
    assert resp.status_code in (302, 303, 307)  # WebAuthRequired -> redirect to login, not a 401 JSON error


def test_preview_renders_markdown(client, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="md-preview-basic")
    _login(client, tenant.slug, users["admin"].email)

    resp = client.post("/ui/markdown-preview", data={"text": "**bold** and _italic_"})
    assert resp.status_code == 200
    assert "<strong>bold</strong>" in resp.text
    assert "<em>italic</em>" in resp.text


def test_preview_renders_valid_embedded_image(client, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="md-preview-image")
    _login(client, tenant.slug, users["admin"].email)

    resp = client.post("/ui/markdown-preview", data={"text": f"![x]({_png_data_uri()})"})
    assert resp.status_code == 200
    assert "data:image/png;base64," in resp.text


def test_preview_shows_friendly_error_for_invalid_image(client, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="md-preview-invalid-image")
    _login(client, tenant.slug, users["admin"].email)

    resp = client.post(
        "/ui/markdown-preview", data={"text": "![x](data:image/svg+xml;base64,PHN2Zz48L3N2Zz4=)"}
    )
    # Not a 500/422 -- the preview itself always succeeds, showing an inline error fragment.
    assert resp.status_code == 200
    assert "Unsupported embedded image type" in resp.text
    assert "flash-error" in resp.text


def test_preview_blank_text_shows_placeholder(client, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="md-preview-blank")
    _login(client, tenant.slug, users["admin"].email)

    resp = client.post("/ui/markdown-preview", data={"text": ""})
    assert resp.status_code == 200
    assert "Nothing to preview yet" in resp.text


def test_preview_is_rate_limited_per_ip(client, tenant_factory, monkeypatch):
    # Public demo credentials make "must be logged in" meaningless as a cost control on
    # this endpoint -- it has its own, stricter per-IP limit on top of the global default
    # every route gets (main.py), since it runs a Pillow decode/re-encode per call.
    monkeypatch.setattr(get_settings(), "markdown_preview_rate_limit_per_minute", 2)
    tenant, _account, users = tenant_factory.make(slug="md-preview-rate-limited")
    _login(client, tenant.slug, users["admin"].email)

    assert client.post("/ui/markdown-preview", data={"text": "one"}).status_code == 200
    assert client.post("/ui/markdown-preview", data={"text": "two"}).status_code == 200
    resp = client.post("/ui/markdown-preview", data={"text": "three"})
    assert resp.status_code == 429
