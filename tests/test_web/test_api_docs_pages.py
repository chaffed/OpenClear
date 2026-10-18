# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed
"""FastAPI's interactive API pages are off when api_docs_enabled is false, which is the default
in production (ASVS 5.0 13.4.5); /openapi.json stays for the SDKs and the docs site."""

import pytest
from fastapi.testclient import TestClient

from openclear.config import get_settings


@pytest.mark.asvs("13.4.5")
def test_api_docs_pages_can_be_turned_off(monkeypatch):
    from openclear.main import create_app

    monkeypatch.setattr(get_settings(), "api_docs_enabled", False)
    client = TestClient(create_app())
    assert client.get("/docs").status_code == 404
    assert client.get("/redoc").status_code == 404
    assert client.get("/openapi.json").status_code == 200


def test_api_docs_pages_are_on_outside_production(monkeypatch):
    from openclear.main import create_app

    monkeypatch.setattr(get_settings(), "api_docs_enabled", None)
    assert TestClient(create_app()).get("/docs").status_code == 200
