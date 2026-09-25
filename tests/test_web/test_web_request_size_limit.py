# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

from openclear.config import get_settings


def test_request_over_max_body_size_is_rejected(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "max_request_body_bytes", 10)

    resp = client.post("/ui/login", content=b"x" * 100, headers={"content-type": "application/x-www-form-urlencoded"})

    assert resp.status_code == 413


def test_request_within_max_body_size_is_not_rejected_by_the_size_check(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "max_request_body_bytes", 10_000)

    # Well within the raised limit — reaches the normal route handling (a 401 for a bad
    # login, not the 413 the size middleware would produce).
    resp = client.get("/ui/login")

    assert resp.status_code == 200
