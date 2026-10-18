# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed
"""/.well-known/security.txt (RFC 9116; OWASP_PLAN.md OW3a): served only once the service
provider names a contact."""

import datetime as dt

from openclear.config import get_settings


def test_not_served_without_a_contact(client):
    assert client.get("/.well-known/security.txt").status_code == 404


def test_served_with_the_service_provider_s_contact(client, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "security_txt_contact", "mailto:security@bank.example, https://bank.example/security")
    monkeypatch.setattr(settings, "security_txt_expires", dt.date(2027, 6, 30))
    monkeypatch.setattr(settings, "security_txt_policy", "https://bank.example/disclosure")
    monkeypatch.setattr(settings, "public_base_url", "https://openclear.bank.example/")

    resp = client.get("/.well-known/security.txt")

    assert resp.status_code == 200 and resp.headers["content-type"].startswith("text/plain")
    assert resp.text.splitlines() == [
        "Contact: mailto:security@bank.example",
        "Contact: https://bank.example/security",
        "Expires: 2027-06-30T00:00:00Z",
        "Policy: https://bank.example/disclosure",
        "Canonical: https://openclear.bank.example/.well-known/security.txt",
        "Preferred-Languages: en",
    ]


def test_expires_defaults_to_a_year_ahead(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "security_txt_contact", "mailto:security@bank.example")
    expires = next(line for line in client.get("/.well-known/security.txt").text.splitlines() if line.startswith("Expires:"))
    assert expires.startswith(f"Expires: {(dt.date.today() + dt.timedelta(days=365)).isoformat()}")
