# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

from openclear.config import get_settings


def test_global_rate_limit_blocks_after_the_configured_count(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "rate_limit_per_minute", 3)

    for _ in range(3):
        assert client.get("/health").status_code == 200
    resp = client.get("/health")
    assert resp.status_code == 429


def test_global_rate_limit_does_not_trip_under_the_default_limit(client):
    for _ in range(5):
        assert client.get("/health").status_code == 200
